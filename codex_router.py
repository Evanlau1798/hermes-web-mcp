"""Choose one verified owner, then keep writes on that backend instance."""
from daemon_adapter import DaemonAdapter, failure
from codex_adapter import CodexAdapter
from rollout_history import history_snapshot


def rejected(result):
    return not isinstance(result,dict) or bool(result.get('errorCode') or result.get('error')) or result.get('ok') is False


class CodexRouter:
    def __init__(self, desktop=None, daemon=None):
        self.desktop = desktop if desktop is not None else CodexAdapter()
        self.daemon = daemon if daemon is not None else DaemonAdapter()

    def __call__(self, action, args):
        result=self._route(action,args)
        if action in ('status','read'):
            result={**result,'liveStatusConfirmed':not rejected(result) and
                    result.get('thread',{}).get('id')==args['thread_id'] and
                    result.get('thread',{}).get('status',{}).get('type') in ('idle','active','systemError')}
            if action=='read' and rejected(result):
                history=history_snapshot(args['thread_id'],message_limit=args.get('message_limit',12),max_chars=args.get('max_chars',1000))
                if history:
                    result['historicalSnapshot']=history
        return result

    def _route(self, action, args):
        args = dict(args)
        if action in ('list','delivery_status'):
            return self.desktop(action,args)
        pinned = args.pop('execution_backend',None)
        instance = args.pop('backend_instance_id',None)
        if pinned:
            if pinned=='desktop':
                before = self.desktop('status',{'thread_id':args['thread_id']})
                if rejected(before):
                    return before
                if instance and before.get('backend_instance_id')!=instance:
                    return failure('BACKEND_INSTANCE_CHANGED')
                if before.get('thread',{}).get('status',{}).get('type')=='notLoaded':
                    return failure('BACKEND_OWNER_CHANGED')
                result = before if action=='status' else self.desktop(action,args)
                return {**result,'execution_backend':'desktop','backend_instance_id':before.get('backend_instance_id')}
            if pinned!='daemon':
                return failure('BACKEND_OWNER_UNCONFIRMED')
            probe = self.daemon.probe(args['thread_id'])
            if not probe.get('available'):
                return failure(probe.get('errorCode') or 'DAEMON_UNAVAILABLE')
            if instance!=probe.get('instance_id'):
                return failure('BACKEND_INSTANCE_CHANGED')
            if action=='load':
                before=self.desktop('status',{'thread_id':args['thread_id']})
                if before.get('thread',{}).get('status',{}).get('type')!='notLoaded':
                    return failure('BACKEND_OWNER_CHANGED')
            elif not probe.get('loaded'):
                return failure('BACKEND_OWNER_CHANGED')
            return self.daemon(action,{**args,'backend_instance_id':instance})
        desktop = self.desktop('status',{'thread_id':args['thread_id']})
        if action in ('status','read') and rejected(desktop):
            return desktop
        dthread = desktop.get('thread') or {}
        desktop_loaded = dthread.get('id')==args['thread_id'] and (dthread.get('status') or {}).get('type') in ('idle','active','systemError')
        probe = self.daemon.probe(args['thread_id'])
        if desktop_loaded and probe.get('loaded'):
            return failure('BACKEND_OWNER_CONFLICT')
        if desktop_loaded:
            if not probe.get('available') and probe.get('errorCode')!='DAEMON_UNAVAILABLE':
                if action not in ('status','read'):
                    return failure(probe.get('errorCode') or 'BACKEND_OWNER_UNCONFIRMED')
                desktop={**desktop,'ownerVerification':{'state':'unknown','errorCode':probe.get('errorCode')}}
            result = desktop if action in ('status','ensure') else self.desktop(action,args)
            if desktop.get('ownerVerification'):
                result={**result,'ownerVerification':desktop['ownerVerification']}
            return {**result,'execution_backend':'desktop','backend_instance_id':desktop.get('backend_instance_id')}
        if probe.get('available') and probe.get('loaded'):
            return self.daemon('status' if action=='ensure' else action,
                               {**args,'backend_instance_id':probe['instance_id']})
        if action=='ensure':
            if not probe.get('available'):
                return failure(probe.get('errorCode') or 'DAEMON_UNAVAILABLE')
            if dthread.get('id')!=args['thread_id'] or dthread.get('title')!=args.get('expected_title') \
                    or (dthread.get('status') or {}).get('type')!='notLoaded':
                return failure('TARGET_LOOKUP_FAILED')
            return self.daemon('load',{**args,'backend_instance_id':probe['instance_id']})
        if action in ('status','read'):
            result=desktop if action=='status' else self.desktop(action,args)
            if probe.get('available') and (dthread.get('status') or {}).get('type')=='notLoaded':
                result={**result,'load_binding':{'execution_backend':'daemon','backend_instance_id':probe['instance_id']}}
            return result
        return failure('BACKEND_OWNER_UNCONFIRMED')

    def ensure_loaded(self, thread_id, expected_title, **binding):
        if binding.get('execution_backend')!='daemon' or not binding.get('backend_instance_id'):
            return failure('BACKGROUND_LOAD_UNAVAILABLE')
        return self('load',{'thread_id':thread_id,'expected_title':expected_title,**binding})

    def close(self):
        self.daemon.close()
