"""Task bootstrap receipts and correlation; identifiers never grant OS permission."""
import copy
import os
import re
import uuid


HARNESS = {
    'name': 'Hermes Harness — ChatGPT Web 工具執行框架',
    'role': 'Web Agent 負責推理；Hermes 提供工具、技能、執行回執與原生防護。',
    'steps': [
        {'tool':'hermes_context', 'use':'新任務或改 cwd 時以 cwd + task 初始化；後續每個工具帶回 bootstrap_id。重新初始化時帶舊 ID，改用新 ID。'},
        {'tool':'hermes_skill_read', 'use':'先讀 prerequisites 的 SKILL.md 至 eof；依正文讀必要 references。AGENTS.md 用 read_file 按 offset 連續讀完。'},
        {'tool':'hermes_read_file / hermes_search_files / hermes_patch / hermes_write_file', 'use':'搜尋定位 → 讀取 → 精確修改 → 讀回驗證；保留無關內容。'},
        {'tool':'hermes_terminal / hermes_process_read', 'use':'PowerShell 執行命令，長工作 background=true；沿 session_id 查 poll/log，以 exit_code 和完整回執判斷。'},
        {'tool':'browser_use / hermes_computer_use', 'use':'網站先讀 browser guide 再沿 TaskSpace 操作；桌面先列視窗、擷取，再依新畫面操作與核對。'},
        {'tool':'codex_prepare_send / codex_operation_status', 'use':'依人類要求操作確切對話；沿 operation_id 查狀態，區分受理、turn 完成和任務結果。'},
        {'tool':'hermes_skill_manage / hermes_memory_edit', 'use':'驗證成果後主動保存可重用技能；跨任務長期事實用記憶，先讀再精確更新，檢查核准與讀回。'},
    ],
    'receipts':'先完整讀回執；圖片當回合顯示，截短沿游標補讀；結果不明先查證，不重送寫入。',
}


def public_specs(specs):
    result = copy.deepcopy(specs)
    for name, spec in result.items():
        schema = spec['input_schema']
        schema['properties']['bootstrap_id'] = {
            'type':'string', 'pattern':'^[0-9a-f]{32}$',
            'description':'逐字帶回本任務 hermes_context.bootstrap.bootstrap_id；重新初始化時帶舊值，之後改用新值。'}
        if name != 'hermes_context':
            schema.setdefault('required', []).append('bootstrap_id')
        else:
            schema.setdefault('required', []).append('task')
            schema['properties']['task']['minLength'] = 1
    return result


def resolve_scope(clients, scope, token, initializing):
    valid = isinstance(token, str) and re.fullmatch('[0-9a-f]{32}', token)
    if scope is None and valid:
        scope = next((key for key, row in clients.items()
                      if key.startswith('explicit:') and row['bootstrap_id'] == token), None)
    row = clients.get(scope)
    if initializing and token is None:
        return scope or 'explicit:' + uuid.uuid4().hex, None
    if row is None or not valid:
        return None, failure('bootstrap_required')
    if row['bootstrap_id'] != token:
        return None, failure('bootstrap_stale')
    return scope, None


def failure(code):
    return {'success':False, 'errorCode':code, 'executionStarted':False,
            'error':'先以 cwd + task 呼叫 hermes_context，後續工具帶回新 bootstrap_id；新任務／改目錄時帶舊 ID 重新初始化。'}


def new_client(result, cwd, task, now):
    return {'seen':now, 'cwd':cwd, 'task':task, 'bootstrap_id':uuid.uuid4().hex,
            'required':result['workflow']['required_skill_ids'], 'pages':{}, 'searched':False,
            'instructions':{os.path.normcase(os.path.abspath(row['path'])):row['path']
                            for row in result['project_instructions']}, 'instruction_pages':{}}


def prerequisites(client, risk):
    skills = [s for s in client['required'] if client['pages'].get(s) != 'complete']
    paths = [path for key, path in client['instructions'].items()
             if client['instruction_pages'].get(key) != 'complete']
    search = bool(risk and not client['required'] and not client['searched'])
    return {'execution_ready':not skills and not paths and not search,
            'skill_match_status':'required' if client['required'] else
                ('searched_no_match' if client['searched'] else 'no_match') if risk else 'not_required',
            'pending':[{'kind':'skill', 'tool':'hermes_skill_read', 'skill_id':s} for s in skills] +
                      [{'kind':'project_instruction', 'tool':'hermes_read_file', 'path':p} for p in paths] +
                      ([{'kind':'skill_search', 'tool':'hermes_skills_list', 'query':client['task']}] if search else [])}


def record_search(client, arguments, result):
    if client['risk'] and not client['required'] and result.get('success') \
            and arguments.get('query', '').strip() == client['task'].strip() and arguments.get('offset', 0) == 0:
        client['searched'] = True
        if result['skills']:
            client['required'] = [result['skills'][0]['id']]


def record_instruction(client, arguments, result):
    key = os.path.normcase(os.path.abspath(arguments['path']))
    if key not in client['instructions'] or result.get('error') or result.get('errorCode') \
            or result.get('success') is False or result.get('ok') is False \
            or not isinstance(result.get('content'), str) or result.get('truncated_lines'):
        return
    offset = arguments.get('offset', 1)
    if offset != client['instruction_pages'].get(key, 1):
        return
    total = result.get('total_lines')
    if type(total) is not int or total < 0:
        return
    if not result.get('truncated'):
        client['instruction_pages'][key] = 'complete'
    elif result.get('next_offset', offset + arguments.get('limit', 2000)) <= total:
        client['instruction_pages'][key] = result.get('next_offset', offset + arguments.get('limit', 2000))


def same_cwd(left, right):
    return bool(left and right) and os.path.normcase(os.path.abspath(left)) == os.path.normcase(os.path.abspath(right))
