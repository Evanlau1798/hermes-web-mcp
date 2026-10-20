"""Explain existing receipts without turning accepted or a newer turn into completion."""


def summarize(row):
    state = row['state']
    compact = ('complete' if row['compact_complete'] else 'running' if state == 'compacting'
               else 'not_required' if row['choice'] == 'send_without_compact'
               else 'failed' if state == 'compaction_failed' else 'unknown'
               if state in ('compaction_unknown', 'compaction_timeout', 'interrupted') else 'pending')
    return {'target': 'verified_at_prepare', 'compact': compact,
            'compact_reason': 'skipped_by_selected_flow' if compact == 'not_required' else None,
            'delivery': row['delivery_state'] or 'not_started', 'turn': 'unobserved',
            'task_completion': 'not_established'}


def observe_turn(api, row):
    try:
        data = api('status', {'thread_id': row['thread_id']})
        thread = data.get('thread') or {}
        if data.get('error') or data.get('errorCode') or data.get('ok') is False or thread.get('id') != row['thread_id'] \
                or thread.get('status', {}).get('type') not in ('idle', 'active'):
            return {'state': 'unknown', 'errorCode': data.get('errorCode') or 'OBSERVATION_FAILED'}
        turn = data.get('latestTurn') or {}
        baseline = row['compact_turn_id'] if row['compact_complete'] else row['baseline_turn_id']
        if not turn.get('id') or turn['id'] == baseline:
            return {'state': 'unobserved', 'correlation': 'no_new_turn'}
        state = {'inProgress': 'started', 'completed': 'complete', 'failed': 'failed',
                 'interrupted': 'interrupted'}.get(turn.get('status'), 'unknown')
        return {'state': state, 'turn_id': turn['id'], 'native_turn_status': turn.get('status'),
                'correlation': 'newer_than_baseline_only'}
    except Exception:
        return {'state': 'unknown', 'errorCode': 'OBSERVATION_FAILED'}
