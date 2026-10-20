"""Keep bounded MCP image payloads outside the text redaction/truncation path."""
import base64
import json
from dataclasses import dataclass


@dataclass
class ComputerResult:
    payload: dict
    images: list


def normalize(raw):
    from backend import safe_result
    if isinstance(raw, str):
        raw = json.loads(raw)
    if not isinstance(raw, dict):
        raise ValueError('電腦操作回傳格式無效。')
    if not raw.get('_multimodal'):
        return ComputerResult(safe_result(raw), [])
    texts, images = [], []
    for content in raw.get('content', []):
        if content.get('type') == 'text':
            texts.append(content['text'])
        elif content.get('type') == 'image_url':
            url = content.get('image_url', {}).get('url', '')
            prefix, separator, encoded = url.partition(',')
            mime = prefix.removeprefix('data:').removesuffix(';base64')
            if not separator or prefix != 'data:' + mime + ';base64' or mime not in ('image/png', 'image/jpeg') \
                    or len(encoded) > 11200000 or images:
                raise ValueError('畫面格式無效，請重新擷取指定視窗。')
            try:
                data = base64.b64decode(encoded, validate=True)
            except ValueError as exc:
                raise ValueError('畫面資料無效，請重新擷取。') from exc
            if len(data) > 8 * 1024 * 1024 or not data.startswith(b'\x89PNG\r\n\x1a\n' if mime == 'image/png' else b'\xff\xd8\xff'):
                raise ValueError('畫面資料無效，請重新擷取。')
            images.append((mime, encoded))
    payload = safe_result({'text': '\n'.join(texts) or raw.get('text_summary', ''),
                           'meta': raw.get('meta', {}), 'image_count': len(images)})
    return ComputerResult(payload, images)
