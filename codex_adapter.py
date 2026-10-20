import json
import subprocess
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from local_paths import executable


class CodexAdapter:
    def __init__(self, root=None, runner=None):
        self.root = Path(root) if root else Path(__file__).parent / 'runtime'
        self.runner = runner or subprocess.run

    def __call__(self, action, arguments):
        if action == 'delivery_status':
            return self.receipt(arguments['operation_id'])
        mutation = action in ('send', 'resume', 'stop', 'compact', 'compact_start')
        operation_id = arguments.get('operation_id') if action in ('send', 'resume') else None
        request_id = 'hermes-' + uuid.uuid4().hex
        db = None
        if operation_id:
            binding = json.dumps([action, arguments.get('thread_id')])
            try:
                self.root.mkdir(parents=True, exist_ok=True)
                db = sqlite3.connect(self.root / 'codex-delivery.sqlite', timeout=2)
                db.execute('CREATE TABLE IF NOT EXISTS receipts (id TEXT PRIMARY KEY, binding TEXT, result TEXT)')
                db.execute('BEGIN IMMEDIATE')
                prior = db.execute('SELECT binding,result FROM receipts WHERE id=?', (operation_id,)).fetchone()
                if prior:
                    db.rollback()
                    db.close()
                    if prior[0] != binding:
                        return self._error('OPERATION_ID_CONFLICT', 'deduplicate', request_id, False, False)
                    return {**json.loads(prior[1]), 'deduplicated': True, 'messageContentVerified': False,
                            'hint': '此 operation_id 已使用；本次內容未派送，只回傳原紀錄。請查證原操作，不要改 ID 盲目重送。'}
                if db.execute('SELECT count(*) FROM receipts').fetchone()[0] >= 10000:
                    db.rollback()
                    db.close()
                    return self._error('RECEIPT_CAPACITY', 'deduplicate', request_id, False, False)
                pending = self._error('DELIVERY_UNCONFIRMED', 'request', request_id, True, False)
                db.execute('INSERT INTO receipts VALUES (?,?,?)', (operation_id, binding, json.dumps(pending)))
                db.commit()
            except (OSError, sqlite3.Error):
                if db:
                    db.close()
                return self._error('RECEIPT_STORE_UNAVAILABLE', 'deduplicate', request_id, False, False)
        try:
            result = self._execute(action, arguments, request_id, mutation)
            if db:
                # Receipts retain no prompt, title, tool output or conversation text.
                public = {k: v for k, v in result.items() if k in (
                    'ok', 'deliveryState', 'automaticRetrySafe', 'requestId', 'errorCode', 'stage',
                    'observedAt', 'requestState', 'executionComplete', 'error', 'suggestedAction', 'source',
                    'threadId', 'turnBaseline')}
                try:
                    db.execute('UPDATE receipts SET result=? WHERE id=?', (json.dumps(public), operation_id))
                    db.commit()
                except sqlite3.Error:
                    result['receiptPersistenceFailed'] = True
            return result
        finally:
            if db:
                db.close()

    def receipt(self, operation_id):
        try:
            db = sqlite3.connect(f'file:{(self.root / "codex-delivery.sqlite").as_posix()}?mode=ro', uri=True)
            try:
                row = db.execute('SELECT result FROM receipts WHERE id=?', (operation_id,)).fetchone()
            finally:
                db.close()
            return {**json.loads(row[0]), 'deduplicated': True} if row else {
                'ok': False, 'errorCode': 'OPERATION_NOT_FOUND', 'error': '找不到操作紀錄；不可據此推論訊息未送達。'}
        except sqlite3.Error:
            return {'ok': False, 'errorCode': 'RECEIPT_STORE_UNAVAILABLE', 'error': '無法讀取送達紀錄；請先查證目標對話。'}

    def _execute(self, action, arguments, request_id, mutation):
        payload = {'action': action, **{k: v for k, v in arguments.items() if k != 'operation_id'}}
        try:
            result = self.runner([str(executable('node')), str(Path(__file__).parent / 'codex.cjs')],
                                 input=json.dumps(payload, ensure_ascii=False), capture_output=True,
                                 text=True, encoding='utf-8', timeout=50,
                                 creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0), cwd=Path(__file__).parent)
        except FileNotFoundError:
            return self._error('NODE_NOT_FOUND', 'launch', request_id, False, not mutation)
        except PermissionError:
            return self._error('PERMISSION_DENIED', 'launch', request_id, False, not mutation)
        except subprocess.TimeoutExpired:
            return self._error('PROCESS_TIMEOUT', 'request', request_id, True, not mutation)
        except OSError:
            return self._error('PROCESS_LAUNCH_FAILED', 'launch', request_id, False, not mutation)
        try:
            data = json.loads(result.stdout)
            if not isinstance(data, dict):
                raise ValueError('invalid envelope')
            return data
        except (ValueError, UnicodeError):
            return self._error('INVALID_BRIDGE_OUTPUT', 'parse', request_id, True, not mutation)

    @staticmethod
    def _error(code, stage, request_id, sent, retry_safe):
        reasons = {
            'NODE_NOT_FOUND': '找不到設定的 Node 執行檔；請確認安裝路徑。',
            'PERMISSION_DENIED': '系統拒絕啟動橋接；請確認目前 Windows 帳號的執行權限。',
            'PROCESS_TIMEOUT': '橋接子程序逾時，Desktop 結果未確認；不能判定 session 閒置。',
            'PROCESS_LAUNCH_FAILED': '橋接子程序無法啟動；具體系統原因不明。',
            'INVALID_BRIDGE_OUTPUT': '橋接回傳非有效的 JSON 物件；結果未確認。',
            'OPERATION_ID_CONFLICT': '相同 operation_id 的目標或操作種類不同，未派送；請先核對原操作。',
            'DELIVERY_UNCONFIRMED': '已有派送紀錄，但結果未確認；請查詢目標，禁止盲目重送。',
            'RECEIPT_STORE_UNAVAILABLE': '無法安全保存送達紀錄，未派送。',
            'RECEIPT_CAPACITY': '送達紀錄已達容量，未派送；請由操作者檢查紀錄。',
        }
        return {'ok': False, 'errorCode': code, 'stage': stage, 'requestId': request_id,
                'source': 'hermes-codex-adapter', 'observedAt': datetime.now(timezone.utc).isoformat(),
                'timedOut': code == 'PROCESS_TIMEOUT', 'disconnected': False,
                'deliveryState': 'unknown' if sent else 'not-sent', 'requestState': 'sent' if sent else 'not-sent',
                'automaticRetrySafe': retry_safe, 'error': reasons[code],
                'suggestedAction': '用 codex_status/read 查證目標；保留 requestId 排錯，不自動重送寫入。'}


def main():
    import sys
    from backend import Bridge
    bridge = Bridge()
    try:
        raw = sys.stdin.buffer.read(1048577)
        if len(raw) > 1048576:
            raise ValueError('輸入超過 1 MiB。')
        payload = json.loads(raw.decode('utf-8-sig'))
        if not isinstance(payload, dict) or payload.get('action') not in (
                'list', 'status', 'read', 'send', 'resume', 'stop', 'delivery_status'):
            raise ValueError('action 無效。')
        action = payload.pop('action')
        result = bridge.call('codex_' + action, payload)
    except (ValueError, UnicodeError):
        result = {'ok': False, 'errorCode': 'INVALID_CLI_INPUT', 'deliveryState': 'not-sent',
                  'automaticRetrySafe': False, 'error': 'CLI JSON 或參數無效；請使用與 MCP 相同的 schema，傳訊須 operation_id 與 expected_title。'}
    finally:
        bridge.close()
    sys.stdout.write(json.dumps(result, ensure_ascii=False))
    return 1 if result.get('ok') is False or result.get('error') else 0


if __name__ == '__main__':
    raise SystemExit(main())
