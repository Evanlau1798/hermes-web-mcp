"""Validate a pinned snapshot while retaining safe native failure facts."""
from compact_state import valid_timestamp, should_compact
from codex_failure import error_details


def read_snapshot(api,thread_id,expected_title,binding):
    try:
        data=api('status',{'thread_id':thread_id,**binding})
    except Exception as exc:
        return {'diagnostic':error_details(exc,'status')},'STATUS_UNAVAILABLE'
    if not isinstance(data,dict) or data.get('ok') is False or data.get('errorCode') or data.get('error'):
        return {'diagnostic':error_details(data,'status')},'STATUS_UNAVAILABLE'
    thread=data.get('thread')
    status=thread.get('status') if isinstance(thread,dict) else None
    turn=data.get('latestTurn')
    compact=data.get('compactState')
    if not isinstance(thread,dict) or not isinstance(status,dict) or not isinstance(status.get('type'),str) \
            or not isinstance(turn,dict) or not isinstance(turn.get('id'),str) or not turn['id'] \
            or not isinstance(turn.get('status'),str) or not isinstance(compact,dict) \
            or not isinstance(compact.get('lastTurnIsCompaction'),bool):
        return None,'SNAPSHOT_INVALID'
    if thread.get('id')!=thread_id:
        return None,'THREAD_ID_MISMATCH'
    if thread.get('title')!=expected_title:
        return None,'THREAD_TITLE_MISMATCH'
    if status['type']=='notLoaded':
        return None,'THREAD_NOT_LOADED'
    completed_at=turn.get('completedAt')
    if completed_at is not None and not valid_timestamp(completed_at):
        return None,'SNAPSHOT_INVALID'
    compact_completed_at=compact.get('completedAt')
    if compact_completed_at is not None and (not valid_timestamp(compact_completed_at) or compact_completed_at!=completed_at):
        return None,'SNAPSHOT_INVALID'
    if compact.get('turnId') is not None and not isinstance(compact.get('turnId'),str):
        return None,'SNAPSHOT_INVALID'
    seen=compact.get('nativeCompactionItemSeen')
    if seen is not None and not isinstance(seen,bool):
        return None,'SNAPSHOT_INVALID'
    if len(turn['id'])>256 or compact.get('turnId') is not None and len(compact['turnId'])>256:
        return None,'SNAPSHOT_INVALID'
    recent=data.get('recentTurnIds')
    previous=recent[1] if isinstance(recent,list) and len(recent)==2 and recent[0]==turn['id'] else None
    native_error=turn.get('nativeError') or turn.get('error')
    diagnostic=error_details(native_error,'native_turn',turn['id']) if native_error else None
    return {'thread_state':status['type'],'turn_id':turn['id'],'turn_status':turn['status'],'previous_turn_id':previous,
            'completed_at':completed_at,'compacted':compact['lastTurnIsCompaction'],'compact_turn_id':compact.get('turnId'),
            'compact_completed_at':compact_completed_at,'auto_compact':should_compact(data),
            'native_compaction_item_seen':seen,'diagnostic':diagnostic},None
