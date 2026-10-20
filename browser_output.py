"""Small browser receipts; original owned process logs stay separately readable."""
from agent.redact import redact_sensitive_text


def format_result(result, session, timings):
    result = {key: value for key, value in result.items() if key != 'command'}
    output = result.get('output', result.get('output_preview', ''))
    if isinstance(output, str):
        output = redact_sensitive_text(output)
        cut = result.get('output_cut', 0)
        cut = cut if type(cut) is int and cut >= 0 else 0
        preview_only = 'output' not in result
        page = result.get('log_page')
        preview = output if 'log_page' in result else output[:6000]
        result.pop('output_preview', None)
        result['output'] = preview
        result['observation'] = {'original_chars': page['total_chars'] if page else None if preview_only else len(output) + cut,
            'returned_chars': len(preview), 'source_cut_chars': cut,
            'truncated': bool(result.get('truncated')) or preview_only or bool(cut) or len(preview) < len(output),
            'length_scope': 'selected_retained_lines' if page else 'preview_only' if preview_only else 'retained_process_buffer'}
        result['truncated'] = result['observation']['truncated']
        result['partial_output'] = result['observation']['truncated']
    page = result.get('log_page')
    selection = {key: page[key] for key in ('offset', 'limit', 'char_offset', 'max_chars') if key in page} if page else {
        'offset': 0, 'limit': 200, 'char_offset': 0, 'max_chars': 6000}
    result['log'] = {'tool': 'hermes_process_read', 'arguments': {
        'action': 'log', 'session_id': session,
        **selection}, 'scope': 'retained_rolling_buffer'}
    result['timings_ms'] = dict(timings)
    return result
