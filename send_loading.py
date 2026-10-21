"""Cold resume is background work; never retain or replay its message on disk."""
import os
import threading
from send_receipts import error, normalize, owner_identity


def start_load(owner, thread_id, title, message, operation_id, binding):
    def run():
        with owner.lock:
            if owner.closed:
                return
        try:
            loaded = owner.api.ensure_loaded(thread_id,title,**binding)
            with owner.lock:
                if owner.closed:
                    result = error('PROCESS_RESTARTED',operation_id,thread_id)
                elif loaded.get('errorCode') or loaded.get('ok') is False:
                    result = error(loaded.get('errorCode') or 'BACKGROUND_LOAD_UNAVAILABLE',operation_id,thread_id)
                else:
                    result = owner._submit_loaded(loaded,thread_id,title,message,operation_id)
                result = {**binding,**normalize(result,operation_id,thread_id)}
                prior = owner.store.get(operation_id) or {}
                result.setdefault('created_at',prior.get('created_at'))
                owner.store.save(result)
        except Exception:
            # A load failure never authorizes replaying a possibly dispatched message.
            owner.store.save(error('DELIVERY_UNCONFIRMED',operation_id,thread_id)|{
                'state':'delivery_unknown','deliveryState':'unknown','delivery_state':'unknown',
                'summary':{'delivery':'unknown'}})
    worker = threading.Thread(target=run,daemon=True,name='codex-load-'+operation_id)
    owner.loading[operation_id] = worker
    worker.start()
    return {'state':'loading','route':'loading','deliveryState':'not_started',
            'worker_alive':True,**owner_identity(),**binding}
