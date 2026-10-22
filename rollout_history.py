"""Bounded public-message fallback; disk history never establishes live state."""
import json
import os
import re
import sqlite3
from datetime import datetime
from contextlib import closing
from pathlib import Path

from agent.redact import redact_sensitive_text
from context_usage import canonical_path


def history_snapshot(thread_id, *, home=None, message_limit=12, max_chars=1000):
    if not isinstance(thread_id,str) or not re.fullmatch(r'[0-9a-f-]{36}',thread_id,re.I):
        return None
    if type(message_limit) is not int or not 1<=message_limit<=20 or type(max_chars) is not int or not 1<=max_chars<=2000:
        return None
    home=canonical_path(home or os.environ.get('CODEX_HOME') or Path.home()/'.codex')
    try:
        with closing(sqlite3.connect((home/'state_5.sqlite').as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
            row=db.execute('SELECT rollout_path FROM threads WHERE id=?',(thread_id,)).fetchone()
        if not row:
            return None
        path=canonical_path(row[0])
        if not any(path.is_relative_to(canonical_path(home/name)) for name in ('sessions','archived_sessions')):
            return None
        with path.open('rb') as stream:
            header=json.loads(stream.readline(65537))
            if header.get('type')!='session_meta' or header.get('payload',{}).get('id')!=thread_id:
                return None
            start=max(0,os.fstat(stream.fileno()).st_size-4*1024*1024)
            stream.seek(start)
            lines=stream.read(4*1024*1024).splitlines()
        if start:
            lines=lines[1:]
        messages=[]
        budget=18000
        clipped=False
        for line in reversed(lines):
            try:
                event=json.loads(line)
                payload=event.get('payload',{})
                if payload.get('phase') not in (None,'commentary','final','final_answer'):
                    continue
                if event.get('type')=='event_msg' and payload.get('type') in ('agent_message','user_message'):
                    role='assistant' if payload['type']=='agent_message' else 'user'
                    text=payload.get('message')
                elif event.get('type')=='response_item' and payload.get('type')=='message' and payload.get('role') in ('user','assistant'):
                    role=payload['role']
                    text='\n'.join(c['text'] for c in payload.get('content',[]) if isinstance(c,dict) and
                                   c.get('type') in ('input_text','output_text','text') and isinstance(c.get('text'),str))
                else:
                    continue
                timestamp=event.get('timestamp')
                if not isinstance(timestamp,str) or datetime.fromisoformat(timestamp.replace('Z','+00:00')).utcoffset() is None:
                    continue
                if not isinstance(text,str) or not text.strip():
                    continue
                text=redact_sensitive_text(text,force=True)
                text=re.sub(r'\b(?:Bearer|Basic)\s+[^\s]+','[REDACTED]',text,flags=re.I)
                message={'role':role,'timestamp':timestamp,'text':text[:max_chars]}
                size=len(json.dumps(message,ensure_ascii=False))
                if size>budget:
                    clipped=True
                    break
                budget-=size
                messages.append(message)
                if len(messages)>=message_limit:
                    break
            except (ValueError,TypeError,AttributeError):
                continue
        if not messages:
            return None
        return {'source':'codex-rollout-history','thread_id':thread_id,'liveStatusConfirmed':False,
                'snapshotAsOf':messages[0]['timestamp'],'messages':messages,
                'boundedHistory':True,'partialHistory':bool(start or clipped or len(messages)>=message_limit),
                'notice':'即時讀取失敗；以下僅為截至 '+messages[0]['timestamp']+' 的歷史快照，不代表目前執行狀態。'}
    except (OSError,ValueError,TypeError,AttributeError,sqlite3.Error):
        return None
