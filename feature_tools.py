"""Additional tool contracts; workers stay owned by the long-lived Bridge."""
import json


TRACKING_STATUS_SCHEMA = {'type': 'object', 'required': ['tracking_id'], 'properties': {
    'tracking_id': {'type': 'string', 'pattern': '^[0-9a-f]{32}$'},
    'after_sequence': {'type': 'integer', 'minimum': 0},
    'limit': {'type': 'integer', 'minimum': 1, 'maximum': 64}}}
TRACKING_SCHEMA = {'type': 'object', 'required': ['action'], 'properties': {
    **TRACKING_STATUS_SCHEMA['properties'],
    'action': {'enum': ['start', 'status', 'stop']}, 'kind': {'enum': ['codex', 'computer']},
    'thread_id': {'type': 'string', 'format': 'uuid'},
    'pid': {'type': 'integer', 'minimum': 1}, 'window_id': {'type': 'integer', 'minimum': 1},
    'mode': {'enum': ['manual', 'task_complete'], 'default': 'manual'},
    'interval': {'type': 'number', 'minimum': 5, 'maximum': 3600},
    'deadline_seconds': {'type': 'number', 'minimum': 1, 'maximum': 86400},
    'expected_turn_id': {'type': 'string', 'minLength': 1, 'maxLength': 256},
    'completion_predicate': {'type': 'object', 'additionalProperties': False,
        'required': ['path'], 'properties': {
            'path': {'type': 'array', 'minItems': 1, 'maxItems': 8,
                     'items': {'oneOf': [{'type': 'string', 'minLength': 1, 'maxLength': 128},
                                          {'type': 'integer', 'minimum': 0}]}},
            'contains': {'type': 'string', 'minLength': 1, 'maxLength': 256},
            'equals': {'type': ['string', 'number', 'boolean', 'null']}},
        'oneOf': [{'required': ['contains']}, {'required': ['equals']}]},
}, 'allOf': [
    {'if': {'properties': {'action': {'const': 'start'}}}, 'then': {
        'required': ['kind'], 'allOf': [
            {'if': {'properties': {'kind': {'const': 'codex'}}}, 'then': {'required': ['thread_id']}},
            {'if': {'properties': {'kind': {'const': 'computer'}}}, 'then': {'required': ['pid', 'window_id']}}]}},
    {'if': {'properties': {'action': {'enum': ['status', 'stop']}}}, 'then': {'required': ['tracking_id']}}
]}


def track(bridge, arguments):
    from backend import safe_result
    args = dict(arguments)
    action = args.pop('action')
    allowed = {'status': {'tracking_id', 'after_sequence', 'limit'}, 'stop': {'tracking_id'},
               'start': {'kind', 'thread_id', 'pid', 'window_id', 'mode', 'interval',
                         'deadline_seconds', 'expected_turn_id', 'completion_predicate'}}
    if set(args) - allowed[action]:
        raise ValueError('追蹤參數不可混用其他 action 的欄位。')
    try:
        if action == 'status':
            return bridge.tracking.status(args['tracking_id'], after_sequence=args.get('after_sequence', 0),
                                          limit=args.get('limit', 64))
        if action == 'stop':
            return bridge.tracking.stop(args['tracking_id'], timeout=1)
        kind = args.pop('kind')
        if kind == 'codex':
            if any(k in args for k in ('pid', 'window_id', 'completion_predicate')):
                raise ValueError('Codex 追蹤只使用 thread_id 與 turn 狀態。')
            target = args.pop('thread_id')
            sampler = lambda: safe_result(bridge.codex('status', {'thread_id': target}))
        else:
            if any(k in args for k in ('thread_id', 'expected_turn_id')):
                raise ValueError('電腦追蹤只使用精確 pid + window_id。')
            target = bridge.owner + '-tracking-computer'
            capture = {'action': 'capture', 'mode': 'ax', 'pid': args.pop('pid'), 'window_id': args.pop('window_id')}
            if bridge.computer is None:
                from computer_adapter import ComputerAdapter
                bridge.computer = ComputerAdapter()
            def sampler():
                raw = bridge.computer.computer_use(capture, session_id=target)
                raw = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(raw, dict) or raw.get('_multimodal') or raw.get('error') or raw.get('success') is False or raw.get('ok') is False:
                    raise ValueError('指定視窗無法取得可驗證的純文字 AX 快照。')
                stable = {key: raw[key] for key in ('mode', 'app', 'window_title', 'elements', 'total_elements', 'text') if key in raw}
                if not stable:
                    raise ValueError('指定視窗未提供可用的 AX 文字或元素。')
                return safe_result(stable)
        return bridge.tracking.start(kind, target, sampler, **args)
    except KeyError:
        return {'success': False, 'errorCode': 'TRACKING_NOT_FOUND',
                'error': '找不到此追蹤；請確認 tracking_id 與 MCP 是否重啟。'}
