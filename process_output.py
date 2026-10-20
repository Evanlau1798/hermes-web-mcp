"""Page retained process text after redaction, including very long single lines."""
import hashlib
from agent.redact import redact_sensitive_text


def page_log(result, *, char_offset=0, max_chars=6000, offset=None, limit=200):
    result = dict(result)
    output = result.get('output')
    if not isinstance(output, str):
        return result
    output = redact_sensitive_text(output)
    end = min(len(output), char_offset + max_chars)
    result['output'] = output[char_offset:end]
    result['log_page'] = {'char_offset': char_offset, 'max_chars': max_chars, 'limit': limit,
        **({'offset': offset} if offset is not None else {}), 'returned_chars': len(result['output']),
        'total_chars': len(output), 'next_char_offset': end if end < len(output) else None,
        'content_id': hashlib.sha256(output.encode('utf-8')).hexdigest()[:16],
        'scope': 'selected_retained_lines'}
    result['truncated'] = char_offset > 0 or end < len(output)
    return result
