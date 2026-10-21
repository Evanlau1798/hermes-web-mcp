"""Read only bounded, persisted resume settings, never conversation text."""
import json
import sqlite3
from daemon_rpc import home
from context_usage import canonical_path


def read_resume_settings(thread_id):
    try:
        root = home()
        db = sqlite3.connect(f'file:{(root/"state_5.sqlite").as_posix()}?mode=ro',uri=True,timeout=1)
        try:
            row = db.execute('SELECT rollout_path FROM threads WHERE id=?',(thread_id,)).fetchone()
        finally:
            db.close()
        if not row:
            return None
        from pathlib import Path
        path = canonical_path(row[0])
        if not any(path.is_relative_to(canonical_path(root/name)) for name in ('sessions','archived_sessions')):
            return None
        with path.open('rb') as stream:
            start = max(0,path.stat().st_size-4*1024*1024)
            stream.seek(start)
            if start:
                stream.readline()
            lines = stream.read(4*1024*1024).splitlines()
        for line in reversed(lines):
            item = json.loads(line)
            if item.get('type')=='turn_context':
                payload = item.get('payload',{})
                policy, sandbox = payload.get('approval_policy'), payload.get('sandbox_policy')
                if isinstance(policy,(str,dict)) and policy and isinstance(sandbox,dict) and sandbox.get('type'):
                    return {'approvalPolicy':policy,'sandbox':sandbox}
                return None
    except (OSError,ValueError,TypeError,sqlite3.Error):
        pass
    return None
