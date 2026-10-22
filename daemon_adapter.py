"""Small native daemon operations; no model/config overrides or generic RPC surface."""
import re
import threading
from agent.redact import redact_sensitive_text
from datetime import datetime, timezone
from context_usage import read_context_usage
from daemon_rpc import DaemonRPC, DaemonError, identity
from resume_settings import read_resume_settings
from codex_failure import error_details

UUID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$',re.I)


def failure(code, sent=False):
    return {'ok':False,'errorCode':code,'deliveryState':'unknown' if sent else 'not-sent',
            'automaticRetrySafe':False,'executionComplete':False,'source':'codex-daemon-live'}


def canonical_policy(value):
    if isinstance(value,dict):
        return {k.replace('_','').lower():canonical_policy(v) if k!='type' else
                v.replace('-','').lower() for k,v in value.items()}
    if isinstance(value,list):
        return [canonical_policy(v) for v in value]
    return value


class DaemonAdapter:
    def __init__(self, client_factory=DaemonRPC, identity=identity):
        self.client_factory, self.identity = client_factory, identity
        self.resume_settings = read_resume_settings
        self.connections = set()
        self.lock = threading.RLock()

    def probe(self, thread_id):
        try:
            instance = self.identity()
            with self.client_factory() as client:
                loaded = client.request('thread/loaded/list',{})
                if not isinstance(loaded.get('data'),list):
                    raise DaemonError('DAEMON_PROTOCOL_ERROR')
                return {'available':True,'loaded':thread_id in loaded['data'],'instance_id':instance}
        except DaemonError as exc:
            return {'available':False,'errorCode':exc.code}

    def _snapshot(self, client, thread_id, args):
        raw = client.request('thread/read',{'threadId':thread_id,'includeTurns':False})
        thread = raw.get('thread') or {}
        if thread.get('id')!=thread_id or not isinstance(thread.get('status'),dict):
            raise DaemonError('THREAD_ID_MISMATCH')
        count = args.get('turn_limit',2)
        if not isinstance(count,int) or isinstance(count,bool) or not 1<=count<=10:
            raise DaemonError('INVALID_ARGUMENT')
        query = {'threadId':thread_id,'limit':count,'sortDirection':'desc','itemsView':'full'}
        if args.get('cursor'):
            query['cursor'] = args['cursor']
        page = client.request('thread/turns/list',query)
        turns = page.get('data')
        if not isinstance(turns,list) or len(turns)>count:
            raise DaemonError('DAEMON_PROTOCOL_ERROR')
        turn = turns[0] if turns else {}
        items = turn.get('items')
        known = isinstance(items,list)
        latest = {k:turn[k] for k in ('id','status','startedAt','completedAt') if k in turn}
        if turn.get('error'):
            latest['nativeError']=error_details(turn['error'],'native_turn',turn.get('id'))
            latest['nativeError'].update(requestPurpose='observation',source='codex-daemon-live')
            if isinstance(client,DaemonRPC):
                latest['nativeError']['requestId']='daemon-rpc-'+str(client.sequence)
        result = {'source':'codex-daemon-live','observedAt':datetime.now(timezone.utc).isoformat(),
            'thread':{'id':thread_id,'title':thread.get('name'),'status':thread['status'],
                      'cwd':thread.get('cwd'),'model':thread.get('model'),'hostId':'local'},
            'latestTurn':latest,'recentTurnIds':[t['id'] for t in turns[:2]],
            'compactState':{'turnId':turn.get('id'),'completedAt':turn.get('completedAt'),
                'lastTurnIsCompaction':(bool(items) and turn.get('status')=='completed' and
                    all(i.get('type')=='contextCompaction' for i in items)) if known else None,
                'nativeCompactionItemSeen':any(i.get('type')=='contextCompaction' for i in items) if known else None},
            'contextUsage':read_context_usage(thread_id,turn.get('id'))}
        if args.get('native_turn_id'):
            candidates, cursor = turns, page.get('nextCursor')
            for _ in range(5):
                own = next((t for t in candidates if t.get('id')==args['native_turn_id']),None)
                if own:
                    result['operationTurn'] = {k:own[k] for k in ('id','status','startedAt','completedAt') if k in own}
                    break
                if not cursor:
                    break
                older = client.request('thread/turns/list',{'threadId':thread_id,'limit':20,
                    'sortDirection':'desc','itemsView':'notLoaded','cursor':cursor})
                candidates, cursor = older.get('data',[]), older.get('nextCursor')
        if args.get('_read'):
            max_chars = args.get('max_chars',1000)
            limit = args.get('message_limit',12)
            if not isinstance(max_chars,int) or not 1<=max_chars<=2000 or not isinstance(limit,int) or not 1<=limit<=20:
                raise DaemonError('INVALID_ARGUMENT')
            result['turns'] = []
            for native in turns:
                messages, agents = [], set()
                for item in native.get('items',[]):
                    kind = item.get('type')
                    if kind=='userMessage' or (kind=='agentMessage' and item.get('phase') in (None,'commentary','final','final_answer')):
                        content = item.get('text') or '\n'.join(c.get('text','') for c in item.get('content',[]) if c.get('type')=='text')
                        content = re.sub(r'\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b','[REDACTED]',content)
                        content = redact_sensitive_text(content,force=True)
                        messages.append({'role':'user' if kind=='userMessage' else 'assistant','text':content[:max_chars]})
                    if kind=='subAgentActivity':
                        agents.add(item.get('agentThreadId'))
                    if kind and kind.startswith('collabAgent'):
                        agents.update([item.get('newThreadId'),item.get('receiverThreadId'),*(item.get('receiverThreadIds') or []),*(item.get('agentsStates') or {})])
                result['turns'].append({**{k:v for k,v in native.items() if k in ('id','status','startedAt','completedAt')},
                    'messages':messages[-limit:],'subagents':[{'threadId':i} for i in agents if isinstance(i,str) and UUID.fullmatch(i)]})
            result['page'] = {'order':'newest_first','nextCursor':page.get('nextCursor'),
                              'hasMore':bool(page.get('nextCursor'))}
        return result

    def __call__(self, action, args):
        if action not in ('status','read','load','send','compact_start','stop') or not UUID.fullmatch(args.get('thread_id','')):
            return failure('INVALID_ARGUMENT')
        client = None
        dispatched = False
        try:
            instance = self.identity()
            if args.get('backend_instance_id') not in (None,instance):
                return failure('BACKEND_INSTANCE_CHANGED')
            with self.client_factory() as client:
                if isinstance(client,DaemonRPC) and client.instance_id!=instance:
                    raise DaemonError('BACKEND_INSTANCE_CHANGED')
                data = self._snapshot(client,args['thread_id'],{**args,'_read':action=='read'})
                data.update(execution_backend='daemon',backend_instance_id=instance)
                if action in ('status','read'):
                    return data
                if data['thread']['title']!=args.get('expected_title'):
                    return failure('THREAD_TITLE_MISMATCH')
                if action=='load':
                    return self._load(client,args,data)
                loaded = client.request('thread/loaded/list',{})
                if args['thread_id'] not in loaded['data']:
                    return failure('BACKEND_OWNER_CHANGED')
                rejoined = client.request('thread/resume',{'threadId':args['thread_id'],'excludeTurns':True})
                if rejoined.get('thread',{}).get('id')!=args['thread_id'] \
                        or rejoined.get('model')!=data['thread'].get('model') \
                        or rejoined.get('cwd')!=data['thread'].get('cwd'):
                    return failure('RESUME_SETTINGS_CHANGED')
                data = self._snapshot(client,args['thread_id'],args)
                if action=='stop':
                    return self._stop(client,args,data)
                turn = data['latestTurn']
                state = data['thread']['status']['type']
                if turn.get('id')!=args.get('expected_baseline_turn_id'):
                    return failure('BASELINE_CHANGED')
                if action=='compact_start':
                    if state!='idle':
                        return failure('THREAD_NOT_IDLE')
                    dispatched = True
                    client.request('thread/compact/start',{'threadId':args['thread_id']})
                    result = {'deliveryState':'accepted','compactComplete':False}
                else:
                    expected_state = 'active' if args.get('active_baseline') else 'idle'
                    if state!=expected_state:
                        return failure('TARGET_CHANGED')
                    message = args.get('message')
                    if not isinstance(message,str) or not message.strip() or len(message.encode('utf-8'))>65536:
                        return failure('INVALID_ARGUMENT')
                    with self.lock:
                        self.connections = {c for c in self.connections if not c.closed.is_set()}
                        if len(self.connections)>=64:
                            return failure('DAEMON_CONNECTION_CAPACITY')
                    params = {'threadId':args['thread_id'],'input':[{'type':'text','text':message,'text_elements':[]}]}
                    if state=='active':
                        params['expectedTurnId'] = turn['id']
                        dispatched = True
                        native = client.request('turn/steer',params)
                        turn_id = native['turnId']
                    else:
                        dispatched = True
                        native = client.request('turn/start',params)
                        turn_id = native['turn']['id']
                    client.watch_turn(args['thread_id'],turn_id)
                    if isinstance(client,DaemonRPC):
                        with self.lock:
                            self.connections = {c for c in self.connections if not c.closed.is_set()}
                            self.connections.add(client)
                    result = {'deliveryState':'accepted','native_turn_id':turn_id}
                return {**result,'execution_backend':'daemon','backend_instance_id':instance,
                        'threadId':args['thread_id'],'turnBaseline':turn,'executionComplete':False,
                        'automaticRetrySafe':False}
        except DaemonError as exc:
            return {**failure(exc.code,exc.sent and dispatched),
                    'error_details':error_details(exc,'request'),
                    'execution_backend':'daemon','backend_instance_id':locals().get('instance')}
        except (OSError,ValueError,KeyError,TypeError):
            return failure('DAEMON_PROTOCOL_ERROR',dispatched)

    def _load(self, client, args, before):
        thread = before['thread']
        loaded = client.request('thread/loaded/list',{})
        if args['thread_id'] in loaded['data']:
            return before
        settings = self.resume_settings(args['thread_id'])
        if not settings or not thread.get('model') or not thread.get('cwd'):
            return failure('RESUME_SETTINGS_UNCONFIRMED')
        resumed = client.request('thread/resume',{'threadId':args['thread_id'],'excludeTurns':True})
        if resumed.get('thread',{}).get('id')!=args['thread_id'] or resumed.get('model')!=thread['model'] \
                or resumed.get('cwd')!=thread['cwd'] or any(canonical_policy(resumed.get(k))!=canonical_policy(v) for k,v in settings.items()):
            return failure('RESUME_SETTINGS_CHANGED')
        after = self._snapshot(client,args['thread_id'],args)
        if after['thread']['title']!=args['expected_title'] or after['latestTurn']!=before['latestTurn']:
            return failure('BASELINE_CHANGED')
        return {**after,'execution_backend':'daemon','backend_instance_id':self.identity()}

    def _stop(self, client, args, before):
        thread_id = args['thread_id']
        paused, interrupted = False, False
        try:
            goal = client.request('thread/goal/get',{'threadId':thread_id}).get('goal')
            if goal and goal.get('status') not in ('paused','complete'):
                client.request('thread/goal/set',{'threadId':thread_id,'status':'paused','origin':'user'})
                paused = True
            if before['thread']['status']['type']=='active':
                client.request('turn/interrupt',{'threadId':thread_id,'turnId':before['latestTurn']['id']})
                interrupted = True
            cleanup = client.request('thread/backgroundTerminals/clean',{'threadId':thread_id})
            after = self._snapshot(client,thread_id,args)
        except DaemonError as exc:
            return {**failure(exc.code,exc.sent),'deliveryState':'partial' if paused or interrupted else
                    ('unknown' if exc.sent else 'not-sent'),'stopConfirmed':False,
                    'goalPauseConfirmed':paused,'execution_backend':'daemon'}
        return {'execution_backend':'daemon','backend_instance_id':self.identity(),'deliveryState':'accepted',
                'stopConfirmed':after['thread']['status']['type']=='idle','executionComplete':False,
                'status':after['thread']['status'],'backgroundCleanup':bool(cleanup is not None),'goalPauseConfirmed':paused,
                'automaticRetrySafe':False}

    def close(self):
        with self.lock:
            for client in self.connections:
                client.close()
            self.connections.clear()
