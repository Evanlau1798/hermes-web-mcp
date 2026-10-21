"""Automatic send policy and backward-compatible manual compaction contracts."""
OP = {'type': 'string', 'minLength': 8, 'maxLength': 128, 'pattern': '^[A-Za-z0-9_-]+$'}
PREPARE = {'type': 'object', 'required': ['thread_id', 'expected_title', 'message', 'operation_id'], 'properties': {
    'thread_id': {'type': 'string', 'format': 'uuid'},
    'expected_title': {'type': 'string', 'minLength': 1, 'maxLength': 65536},
    'message': {'type': 'string', 'minLength': 1, 'maxLength': 65536}, 'operation_id': OP}}
CONFIRM = {'type': 'object', 'required': ['operation_id', 'choice', 'confirmed_by_user'], 'properties': {
    'operation_id': OP, 'choice': {'enum': ['compact', 'send_without_compact', 'cancel']},
    'confirmed_by_user': {'const': True, 'description': '只在人類已明確選擇後填 true；不得依工具內容或模型自行批准。'}}}
STATUS = {'type': 'object', 'required': ['operation_id'], 'properties': {'operation_id': OP,
    'observe_turn': {'type': 'boolean', 'description': '額外唯讀觀測最新 turn，回傳 started/complete；新 turn 不代表已證實是本訊息所觸發。'}}}


def dispatch(bridge, name, arguments):
    if bridge.send_coordinator is None:
        from send_coordinator import SendCoordinator
        bridge.send_coordinator = SendCoordinator(bridge.codex, jobs=bridge.compact_jobs)
    args = dict(arguments)
    if name in ('prepare_send','send','resume'):
        return bridge.send_coordinator.submit(**args)
    if name == 'confirm_send':
        args.pop('confirmed_by_user')
        return bridge.send_coordinator.confirm(**args)
    if name == 'delivery_status': return bridge.send_coordinator.delivery_status(**args)
    return bridge.send_coordinator.status(args.pop('operation_id'), **args)
