"""Read bounded native usage metadata; never return conversation content."""
import json
import os
import sqlite3
from pathlib import Path


def canonical_path(value):
    value = str(value)
    if os.name=='nt' and value.startswith('\\\\?\\'):
        value = '\\\\'+value[8:] if value.startswith('\\\\?\\UNC\\') else value[4:]
    return Path(value).resolve()


def read_context_usage(thread_id, turn_id, home=None):
    unknown = {'contextTokens': None, 'source': 'native-last-token-usage'}
    home = canonical_path(home or os.environ.get('CODEX_HOME') or Path.home() / '.codex')
    try:
        db = sqlite3.connect(f'file:{(home / "state_5.sqlite").as_posix()}?mode=ro', uri=True, timeout=1)
        try:
            row = db.execute('SELECT rollout_path FROM threads WHERE id=?', (thread_id,)).fetchone()
        finally:
            db.close()
        if not row or not turn_id:
            return unknown
        path = canonical_path(row[0])
        if not any(path.is_relative_to(canonical_path(home / root)) for root in ('sessions', 'archived_sessions')):
            return unknown
        with path.open('rb') as stream:
            start = max(0, path.stat().st_size - 4 * 1024 * 1024)
            stream.seek(start)
            if start:
                stream.readline()
            lines = stream.read(4 * 1024 * 1024).splitlines()
        tokens = None
        for line in reversed(lines):
            try:
                event = json.loads(line)
            except (ValueError, UnicodeError):
                return unknown
            if not isinstance(event, dict) or event.get('type') != 'event_msg':
                continue
            payload = event.get('payload')
            if not isinstance(payload, dict):
                return unknown
            if payload.get('type') == 'task_started':
                return {**unknown, 'contextTokens': tokens} if payload.get('turn_id') == turn_id else unknown
            if payload.get('type') == 'token_count' and tokens is None:
                info = payload.get('info')
                usage = info.get('last_token_usage') if isinstance(info, dict) else None
                value = usage.get('total_tokens') if isinstance(usage, dict) else None
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    return unknown
                tokens = value
    except (OSError, sqlite3.Error, ValueError, TypeError):
        pass
    return unknown
