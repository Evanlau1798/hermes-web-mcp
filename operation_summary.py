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
        binding = {k:row[k] for k in ('execution_backend','backend_instance_id') if row.get(k)}
        if row.get('native_turn_id'):
            binding['native_turn_id']=row['native_turn_id']
        data = api('status', {'thread_id': row['thread_id'], **binding})
        thread = data.get('thread') or {}
        if data.get('error') or data.get('errorCode') or data.get('ok') is False or thread.get('id') != row['thread_id'] \
                or thread.get('status', {}).get('type') not in ('idle', 'active'):
            return {'state': 'unknown', 'errorCode': data.get('errorCode') or 'OBSERVATION_FAILED'}
        turn = (data.get('operationTurn') if row.get('native_turn_id') else None) or data.get('latestTurn') or {}
        if row.get('native_turn_id') and turn.get('id')!=row['native_turn_id']:
            return {'state':'unknown','correlation':'native_turn_not_in_latest_snapshot',
                    'turn_id':row['native_turn_id']}
        baseline = row['compact_turn_id'] if row['compact_complete'] else row['baseline_turn_id']
        if not turn.get('id') or (turn['id'] == baseline and not row.get('native_turn_id')):
            return {'state': 'unobserved', 'correlation': 'no_new_turn'}
        state = {'inProgress': 'started', 'completed': 'complete', 'failed': 'failed',
                 'interrupted': 'interrupted'}.get(turn.get('status'), 'unknown')
        return {'state': state, 'turn_id': turn['id'], 'native_turn_status': turn.get('status'),
                'correlation': 'native_turn_id' if row.get('native_turn_id') else 'newer_than_baseline_only'}
    except Exception:
        return {'state': 'unknown', 'errorCode': 'OBSERVATION_FAILED'}


def compact_receipt(row, worker_alive=False):
    complete = bool(row['compact_complete'])
    compact_state = {'turnId': row['compact_turn_id'] if complete else row['baseline_compact_turn_id'],
                     'completedAt': row['compact_completed_at'] if complete else row['baseline_compact_completed_at'],
                     'lastTurnIsCompaction': complete or bool(row['baseline_compacted']),
                     'nativeCompactionItemSeen': True if complete else row['baseline_native_compaction_item_seen']}
    return {**{k:row[k] for k in ('execution_backend','backend_instance_id','native_turn_id') if row.get(k)},
            'ok': row['state'] not in ('rejected', 'canceled', 'interrupted', 'compaction_failed',
                                       'compaction_unknown', 'compaction_timeout', 'delivery_unknown',
                                       'delivery_not_sent', 'delivery_failed'),
            'operation_id': row['operation_id'], 'thread_id': row['thread_id'],
            'delivery_operation_id': row['delivery_operation_id'],
            'baseline_turn_id': row['baseline_turn_id'], 'compact_turn_id': row['compact_turn_id'],
            'baseline_completed_at': row['baseline_completed_at'],
            'baseline_compacted': bool(row['baseline_compacted']), 'compactState': compact_state,
            'state': row['state'], 'worker_alive': worker_alive,
            'compact_complete': bool(row['compact_complete']), 'delivery_state': row['delivery_state'],
            'reconfirmation_required': row['state'] == 'interrupted',
            'errorCode': row['error_code'], 'created_at': row['created_at'],
            'updated_at': row['updated_at'], 'confirmed_at': row['confirmed_at'],
            'compact_started_at': row['compact_started_at'],
            'summary': summarize(row), 'compact_completed_at': row['compact_completed_at'],
            'delivery_started_at': row['delivery_started_at'],
            'delivery_completed_at': row['delivery_completed_at']}
