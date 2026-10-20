"""Ego's official JS runtime, executed through the owned Hermes terminal."""
import base64
import json
import os
import shutil
import tempfile
import uuid
import time
from pathlib import Path

from computer_result import ComputerResult
from terminal_adapter import _guard_original, prepare_terminal


SCHEMA = {'type': 'object', 'required': ['action'], 'properties': {
    'action': {'enum': ['guide', 'run']},
    'code': {'type': 'string', 'minLength': 1, 'maxLength': 100000, 'pattern': r'\S',
             'description': '官方 ego-browser JavaScript；每次執行為新 Node.js 程序。'},
}, 'oneOf': [{'properties': {'action': {'const': 'guide'}}, 'not': {'required': ['code']}},
             {'properties': {'action': {'const': 'run'}}, 'required': ['code']}]}


def ego_executable():
    configured = os.environ.get('HERMES_MCP_EGO_BROWSER')
    if configured is not None:
        path = Path(configured)
        if not configured or not path.is_absolute() or not path.is_file() or path.suffix.lower() != '.exe':
            raise ValueError('HERMES_MCP_EGO_BROWSER 必須指定有效的絕對 .exe 路徑。')
        return path
    found = shutil.which('ego-browser.exe')
    path = Path(found) if found else Path.home() / '.local/share/ego/active_version_dir/ego-browser.exe'
    if not path.is_file():
        raise FileNotFoundError('請安裝 Ego Lite 並完成初始化，或設定 HERMES_MCP_EGO_BROWSER。')
    return path


def _data(raw):
    value = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(value, dict):
        raise ValueError('Hermes 程序回傳格式無效。')
    return value


class BrowserAdapter:
    def __init__(self, root=None):
        from runtime import ROOT
        self.root = root or ROOT / 'tmp/browser-use'
        self.runs = {}
        self.sessions = {}

    def call(self, args, native, owner, approve):
        from backend import safe_result
        folder = None
        session = None
        started = False
        beginning = time.perf_counter()
        timings = {}
        try:
            executable = ego_executable()
            if args['action'] == 'run':
                if len(self.runs) >= 128:
                    raise ValueError('請先查詢既有 browser use 程序至完成，再開始新操作。')
                _guard_original(args['code'], {'timeout': 30}, owner)
                self.root.mkdir(parents=True, exist_ok=True)
                folder = Path(tempfile.mkdtemp(prefix='round-', dir=self.root))
                script = folder / 'round.mjs'
                image = (folder / 'screenshot.png').as_posix()
                script.write_text('const browserScreenshotPath = ' + json.dumps(image) + ';\n' + args['code'],
                                  encoding='utf-8', newline='')
                # The ASCII import avoids Windows command-line Unicode/quote corruption.
                entry = "await import('" + script.as_uri().replace("'", '%27') + "')"
                suffix = " nodejs -e '" + entry.replace("'", "''") + "'"
            else:
                suffix = ' skill'
            command = "& '" + str(executable).replace("'", "''") + "'" + suffix
            prepared, _ = prepare_terminal({'command': command, 'timeout': 30,
                **({'background': True} if folder else {})}, session_key=owner)
            launching = time.perf_counter()
            timings['prepare_ms'] = round((launching - beginning) * 1000, 3)
            started = True
            result = _data(native('terminal', prepared, approval_callback=approve,
                task_id=owner, session_id=owner, tool_call_id=uuid.uuid4().hex, enabled_tools=['terminal']))
            session = result.get('session_id')
            timings['launch_ms'] = round((time.perf_counter() - launching) * 1000, 3)
            if folder and session:
                self.runs[session] = folder
                for old in list(self.sessions):
                    if len(self.sessions) < 128:
                        break
                    if old not in self.runs:
                        del self.sessions[old]
                self.sessions[session] = timings
                waiting = time.perf_counter()
                result = _data(native('process_manage', {'action': 'wait', 'session_id': session, 'timeout': 25},
                    approval_callback=approve, task_id=owner, session_id=owner))
                timings['wait_ms'] = round((time.perf_counter() - waiting) * 1000, 3)
                timings['local_total_ms'] = round((time.perf_counter() - beginning) * 1000, 3)
                result['session_id'] = session
                return self.collect(session, result)
            if folder and result.get('error'):
                shutil.rmtree(folder)
            return ComputerResult(safe_result({**result, 'automaticRetrySafe': False,
                'timings_ms': timings,
                'browser_guidance': '先讀 guide；同一任務沿用 TaskSpace ID。截圖用 page.screenshot({path: browserScreenshotPath})。長工作以 hermes_process_read 查詢。'}), [])
        except Exception as exc:
            if folder and not started:
                shutil.rmtree(folder)
            return ComputerResult(safe_result({'success': False, 'error': str(exc),
                'executionStarted': started, 'automaticRetrySafe': False,
                **({'session_id': session} if session else {}),
                'outcome': 'unknown' if started else 'not-sent'}), [])

    def collect(self, session, result):
        from backend import safe_result
        folder = self.runs.get(session)
        if folder is None and session not in self.sessions:
            return None
        result = {**result, 'session_id': session, 'automaticRetrySafe': False}
        images = []
        if folder is not None and result.get('status') in ('exited', 'completed', 'already_exited') \
                and result.get('completion_reason', 'exited') == 'exited':
            image = folder / 'screenshot.png'
            try:
                if image.exists() or image.is_symlink():
                    if folder.is_symlink() or folder.is_junction() or image.is_symlink() or image.is_junction() \
                            or image.resolve().parent != folder.resolve() or image.stat().st_size > 8 * 1024 * 1024:
                        raise ValueError('截圖來源或大小無效。')
                    data = image.read_bytes()
                    if not data.startswith(b'\x89PNG\r\n\x1a\n') or len(data) > 8 * 1024 * 1024:
                        raise ValueError('截圖必須為有效大小的 PNG。')
                    images.append(('image/png', base64.b64encode(data).decode('ascii')))
            except (OSError, ValueError) as exc:
                result['image_error'] = str(exc)
            finally:
                cleanup_error = self._cleanup(session)
                if cleanup_error:
                    result['cleanup_error'] = cleanup_error
        result['image_count'] = len(images)
        from browser_output import format_result
        result = format_result(result, session, self.sessions.get(session, {}))
        payload = safe_result(result)
        payload.update(session_id=session, automaticRetrySafe=False, image_count=len(images))
        return ComputerResult(payload, images)

    def _cleanup(self, session):
        folder = self.runs[session]
        try:
            shutil.rmtree(folder)
        except FileNotFoundError:
            pass
        except OSError as exc:
            return str(exc)
        self.runs.pop(session)
        return None

    def close(self, registry):
        for session in list(self.runs):
            process = registry.get(session)
            # Killing the CLI does not prove the browser daemon stopped its script.
            if process and process.exited and process.completion_reason == 'exited':
                self._cleanup(session)
