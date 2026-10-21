"""One send policy for Web and CLI aliases; messages exist only in worker memory."""
import os
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from compact_jobs import CompactJobs
from send_receipts import SendReceipts, error, normalize, owner_identity, owner_alive
from operation_summary import observe_turn


DEFAULT_MESSAGE='使用者要求續跑：請依照這段對話的既有目標與最新指示，從目前進度繼續。'


class SendCoordinator:
    def __init__(self,api,root=None,jobs=None):
        self.api=api
        self.root=Path(root) if root else Path(__file__).parent/'runtime'
        self.store=SendReceipts(self.root)
        self.jobs=jobs
        self.lock=threading.RLock()
        self.loading={}
        self.closed=False

    def _jobs(self):
        if self.jobs is None:
            # Another CLI/bridge may own live jobs; construction must never interrupt them.
            self.jobs=CompactJobs(self.api,root=self.root,recover=False)
        return self.jobs

    def status(self,operation_id,observe_turn=False):
        try:
            intent=self.store.get(operation_id)
            if not intent:
                parent=self.store.parent(operation_id)
                if parent: return self.status(parent['operation_id'],observe_turn=observe_turn)
            legacy=self.store.legacy(operation_id)
            if intent and intent.get('route')=='idle':
                if legacy and legacy.get('errorCode')=='RECEIPT_CONFLICT': return legacy
                if self.jobs and operation_id in self.jobs.jobs:
                    result=self.jobs.status(operation_id,observe_turn=observe_turn)
                else:
                    result=legacy or intent
                    if result.get('state') in (*CompactJobs.ACTIVE,'preparing') and intent.get('owner_pid'):
                        if owner_alive(intent) is False:
                            result.update(state='interrupted',errorCode='PROCESS_RESTARTED',worker_alive=False,
                                delivery_state='unknown' if result.get('delivery_started_at') else 'not-sent')
                            result.setdefault('summary',{})['delivery']=result['delivery_state']
                    if observe_turn and legacy:
                        result['turn_observation']=observe_turn_fn(self.api,intent['thread_id'],
                            {'id':result.get('compact_turn_id') or result.get('baseline_turn_id')},result)
                return normalize(result,operation_id,intent['thread_id'])
            if intent:
                if legacy: return error('RECEIPT_CONFLICT',operation_id,intent['thread_id'])
                result=normalize(intent,operation_id)
                if result.get('route')=='active' and result.get('state')=='sending' and owner_alive(intent) is False:
                    result.update(state='delivery_unknown',errorCode='PROCESS_RESTARTED',deliveryState='unknown',
                                  delivery_state='unknown',summary={'delivery':'unknown'})
                if observe_turn and result.get('turnBaseline'):
                    baseline=result['turnBaseline']
                    result['turn_observation']=observe_turn_fn(self.api,result['thread_id'],baseline,result)
                if result.get('state')=='loading':
                    worker=self.loading.get(operation_id)
                    result['worker_alive']=bool(worker and worker.is_alive())
                    if not result['worker_alive'] and owner_alive(intent) is False:
                        result.update(state='interrupted',errorCode='PROCESS_RESTARTED',deliveryState='not-sent',
                                      delivery_state='not-sent',summary={'delivery':'not-sent'})
                    elif not result['worker_alive']:
                        result.update(worker_alive=None,owner_alive=owner_alive(intent))
                return result
            if legacy: return normalize(legacy,legacy.get('operation_id',operation_id))
            return error('OPERATION_NOT_FOUND',operation_id)
        except (OSError,sqlite3.Error,ValueError,TypeError):
            result=error('RECEIPT_STORE_UNAVAILABLE',operation_id)
            result.update(deliveryState='unknown',delivery_state='unknown',summary={'delivery':'unknown'})
            return result

    def delivery_status(self,operation_id):
        return self.status(operation_id)

    def submit(self,thread_id,expected_title,operation_id,message=None):
        message=message if message is not None else DEFAULT_MESSAGE
        if not isinstance(message,str) or not message.strip() or len(message.encode('utf-8'))>65536:
            return error('INVALID_ARGUMENT',operation_id,thread_id)
        with self.lock:
            try:
                previous=self.store.get(operation_id) or self.store.parent(operation_id) or self.store.legacy(operation_id)
                if previous:
                    if previous.get('thread_id') not in (None,thread_id):
                        return error('OPERATION_ID_CONFLICT',operation_id,thread_id)
                    return {**self.status(operation_id),'deduplicated':True,'messageContentVerified':False}
                receipt,fresh=self.store.reserve(operation_id,thread_id)
                if not fresh:
                    if receipt.get('thread_id')!=thread_id: return error('OPERATION_ID_CONFLICT',operation_id,thread_id)
                    return {**self.status(operation_id),'deduplicated':True,'messageContentVerified':False}
                result=self._submit(thread_id,expected_title,message,operation_id)
                result=normalize(result,operation_id,thread_id)
                result.setdefault('created_at',receipt['created_at'])
                if result['created_at'] is None: result['created_at']=receipt['created_at']
                result['updated_at']=time.time()
                self.store.save(result)
                return result
            except Exception:
                # Reservation is durable before any dispatch; failures are never replayed.
                result=error('DELIVERY_UNCONFIRMED',operation_id,thread_id)
                result.update(state='delivery_unknown',deliveryState='unknown',delivery_state='unknown',summary={'delivery':'unknown'})
                return result

    def _submit(self,thread_id,title,message,operation_id):
        data=self.api('status',{'thread_id':thread_id})
        thread=data.get('thread') or {}
        state=(thread.get('status') or {}).get('type')
        if data.get('errorCode')=='THREAD_NOT_LOADED':
            return error('BACKGROUND_LOAD_UNAVAILABLE',operation_id,thread_id)
        if data.get('errorCode') or data.get('ok') is False: return error(data.get('errorCode') or 'STATUS_UNAVAILABLE',operation_id,thread_id)
        if thread.get('id')!=thread_id: return error('THREAD_ID_MISMATCH',operation_id,thread_id)
        if thread.get('title')!=title: return error('THREAD_TITLE_MISMATCH',operation_id,thread_id)
        if state=='notLoaded':
            if hasattr(type(self.api),'ensure_loaded'):
                from send_loading import start_load
                binding=data.get('load_binding')
                if not binding: return error('BACKGROUND_LOAD_UNAVAILABLE',operation_id,thread_id)
                return start_load(self,thread_id,title,message,operation_id,binding)
            return error('BACKGROUND_LOAD_UNAVAILABLE',operation_id,thread_id)
        return self._submit_loaded(data,thread_id,title,message,operation_id)

    def _submit_loaded(self,data,thread_id,title,message,operation_id):
        thread=data.get('thread') or {}
        if thread.get('id')!=thread_id or thread.get('title')!=title:
            return error('TARGET_LOOKUP_FAILED',operation_id,thread_id)
        state=(thread.get('status') or {}).get('type')
        binding={k:data[k] for k in ('execution_backend','backend_instance_id') if data.get(k)}
        if state=='active':
            turn=data.get('latestTurn') or {}
            if not isinstance(turn.get('id'),str) or not turn['id'] or len(turn['id'])>256:
                return error('SNAPSHOT_INVALID',operation_id,thread_id)
            args=dict(thread_id=thread_id,expected_title=title,message=message,
                      operation_id='send-delivery-'+uuid.uuid4().hex,active_baseline=True,
                      expected_baseline_turn_id=turn['id'],**binding)
            self.store.save({**self.store.get(operation_id),'route':'active','state':'sending',
                             'deliveryState':'unknown','delivery_state':'unknown','summary':{'delivery':'unknown'},
                             'delivery_operation_id':args['operation_id'],**binding,**owner_identity(),'turnBaseline':{
                                 key:turn[key] for key in ('id','status','startedAt','completedAt') if key in turn}})
            result=self.api('send',args)
            if result.get('deduplicated'):
                result={**result,'deliveryState':'unknown','errorCode':'DELIVERY_ID_REUSED'}
            # Do not retain native receipt text/title; the adapter holds its own safe receipt.
            return {**binding,**{k:v for k,v in result.items() if k in ('deliveryState','errorCode','requestId','turnBaseline','error','native_turn_id')},
                    'route':'active','delivery_operation_id':args['operation_id']}
        if state!='idle': return error('THREAD_STATE_UNCONFIRMED',operation_id,thread_id)
        jobs=self._jobs()
        # Publish the shared job route before its row becomes visible to other clients.
        self.store.save({**self.store.get(operation_id),**binding,'route':'idle','state':'preparing',
                         'deliveryState':'not_started','delivery_state':'not_started',
                         'summary':{'delivery':'not_started'},**owner_identity()})
        result=jobs.prepare(thread_id,title,message,operation_id,**binding)
        if result.get('state')=='awaiting_confirmation': result=jobs.confirm(operation_id,'auto')
        return {**result,'route':'idle',**owner_identity()}

    def confirm(self,operation_id,choice):
        result=self._jobs().confirm(operation_id,choice)
        return normalize(result,operation_id)

    def close(self):
        self.closed=True
        if self.jobs: return self.jobs.close()


def observe_turn_fn(api,thread_id,baseline,binding=None):
    return observe_turn(api,{**(binding or {}),'thread_id':thread_id,'baseline_turn_id':baseline.get('id'),
                             'compact_complete':False,'compact_turn_id':None})
