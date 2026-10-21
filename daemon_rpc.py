"""Bounded WebSocket RPC over the official proxy to an existing local daemon."""
import json
import os
import platform
import queue
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
import psutil
from websockets.client import ClientProtocol
from websockets.frames import Frame, OP_TEXT, OP_CONT
from websockets.uri import parse_uri


class DaemonError(Exception):
    def __init__(self, code, sent=False):
        self.code, self.sent = code, sent
        super().__init__(code)


def home():
    return Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex').resolve()


def identity():
    try:
        data = json.loads((home()/'app-server-daemon/daemon.pid').read_text('utf-8'))
        proc = psutil.Process(int(data['pid']))
        created = proc.create_time()
        stored = data['processStartTime']
        recorded = (int(stored)/10000000-11644473600) if int(stored)>10**15 else float(stored)
        if abs(created-recorded)>1 or proc.name().lower()!='codex.exe':
            raise ValueError('identity mismatch')
        return f'daemon:{proc.pid}:{created:.6f}'
    except (FileNotFoundError,psutil.NoSuchProcess):
        code='DAEMON_IDENTITY_UNCONFIRMED' if (home()/'app-server-control/app-server-control.sock').exists() else 'DAEMON_UNAVAILABLE'
        raise DaemonError(code) from None
    except (OSError, ValueError, KeyError, TypeError, psutil.Error):
        raise DaemonError('DAEMON_IDENTITY_UNCONFIRMED') from None


def executable():
    configured = os.environ.get('HERMES_MCP_CODEX')
    if configured is not None:
        path = Path(configured)
        if path.is_absolute() and path.is_file() and path.suffix.lower()=='.exe':
            return path
        raise DaemonError('CODEX_EXECUTABLE_INVALID')
    direct = shutil.which('codex.exe')
    if direct:
        return Path(direct)
    shim = shutil.which('codex.cmd') or shutil.which('codex')
    arch = 'arm64' if platform.machine().lower() in ('arm64','aarch64') else 'x64'
    if shim:
        package = Path(shim).parent/'node_modules/@openai/codex'
        matches = list((package/f'node_modules/@openai/codex-win32-{arch}/vendor').glob('*/bin/codex.exe'))
        if len(matches)==1:
            return matches[0]
    raise DaemonError('CODEX_EXECUTABLE_NOT_FOUND')


class DaemonRPC:
    def __init__(self):
        self.proc = None
        self.closed = threading.Event()
        self.retained = False
        self.pending_messages = []
        self.fragments = b''
        self.sequence = 0
        self.blocked_requests = 0
        self.completed_events = deque(maxlen=64)

    def __enter__(self):
        self.instance_id = identity()
        try:
            self.proc = subprocess.Popen([str(executable()),'app-server','proxy'],
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            self.protocol = ClientProtocol(parse_uri('ws://localhost/'), max_size=8*1024*1024)
            self.chunks = queue.Queue(maxsize=32)
            threading.Thread(target=self._reader, daemon=True).start()
            self.protocol.send_request(self.protocol.connect())
            self._flush()
            deadline = time.monotonic()+10
            while self.protocol.state.name!='OPEN':
                self._receive(deadline-time.monotonic())
            self.request('initialize', {'clientInfo': {'name':'hermes-daemon-tools','version':'1'},
                                        'capabilities': {'experimentalApi':True}})
            self._send({'jsonrpc':'2.0','method':'initialized'})
            if identity()!=self.instance_id:
                raise DaemonError('BACKEND_INSTANCE_CHANGED')
            return self
        except Exception as exc:
            self.close()
            if isinstance(exc,DaemonError):
                raise
            raise DaemonError('DAEMON_CONNECT_FAILED') from None

    def _reader(self):
        try:
            while not self.closed.is_set():
                chunk = self.proc.stdout.read1(65536)
                while not self.closed.is_set():
                    try:
                        self.chunks.put(chunk,timeout=.5)
                        break
                    except queue.Full:
                        continue
                if not chunk:
                    return
        except (OSError,ValueError):
            self.closed.set()

    def _flush(self):
        for chunk in self.protocol.data_to_send():
            self.proc.stdin.write(chunk)
        self.proc.stdin.flush()

    def _send(self, data):
        self.protocol.send_text(json.dumps(data,ensure_ascii=False).encode('utf-8'))
        self._flush()

    def _receive(self, timeout):
        if timeout<=0 or self.closed.is_set():
            raise DaemonError('DAEMON_TIMEOUT')
        try:
            chunk = self.chunks.get(timeout=timeout)
        except queue.Empty:
            raise DaemonError('DAEMON_TIMEOUT') from None
        if not chunk:
            raise DaemonError('DAEMON_DISCONNECTED')
        self.protocol.receive_data(chunk)
        for event in self.protocol.events_received():
            if isinstance(event,Frame) and event.opcode in (OP_TEXT,OP_CONT):
                self.fragments += event.data
                if len(self.fragments)>8*1024*1024:
                    raise DaemonError('DAEMON_OUTPUT_LIMIT')
                if event.fin:
                    message = json.loads(self.fragments)
                    self.fragments = b''
                    if 'method' in message and 'id' in message:
                        self.blocked_requests += 1
                        self._send({'jsonrpc':'2.0','id':message['id'], 'error': {
                            'code':-32000,'message':'Hermes native approval/tool channel unavailable'}})
                    else:
                        if message.get('method')=='turn/completed':
                            self.completed_events.append(message)
                        if len(self.pending_messages)>=512:
                            raise DaemonError('DAEMON_OUTPUT_LIMIT')
                        self.pending_messages.append(message)
        self._flush()

    def request(self, method, params, timeout=30):
        self.sequence += 1
        request_id = self.sequence
        sent = False
        try:
            sent = True
            self._send({'jsonrpc':'2.0','id':request_id,'method':method,'params':params})
            deadline = time.monotonic()+timeout
            while True:
                for i,event in enumerate(self.pending_messages):
                    if event.get('id')==request_id:
                        self.pending_messages.pop(i)
                        if 'error' in event:
                            native = event['error']
                            code = 'DAEMON_CAPABILITY_UNAVAILABLE' if native.get('code')==-32601 else (
                                'BACKEND_WRITER_CONFLICT' if 'active writer' in str(native.get('message','')) else 'DAEMON_REJECTED')
                            raise DaemonError(code,False)
                        return event['result']
                # Unrelated notifications are not persisted or accumulated during RPC calls.
                self.pending_messages = [e for e in self.pending_messages if 'id' in e]
                self._receive(deadline-time.monotonic())
        except DaemonError as exc:
            if exc.code not in ('DAEMON_REJECTED','DAEMON_CAPABILITY_UNAVAILABLE','BACKEND_WRITER_CONFLICT'):
                exc.sent = sent
            raise
        except (OSError,ValueError,KeyError):
            raise DaemonError('DAEMON_PROTOCOL_ERROR',sent) from None

    def watch_turn(self, thread_id, turn_id):
        self.retained = True
        def watch():
            try:
                while not self.closed.is_set():
                    for event in self.completed_events:
                        params = event.get('params',{})
                        if event.get('method')=='turn/completed' and params.get('threadId')==thread_id \
                                and params.get('turn',{}).get('id')==turn_id:
                            return
                    self.pending_messages.clear()
                    try:
                        self._receive(30)
                    except DaemonError as exc:
                        if exc.code!='DAEMON_TIMEOUT':
                            raise
            except DaemonError:
                pass
            finally:
                self.close()
        threading.Thread(target=watch,daemon=True,name='daemon-turn-'+turn_id[:36]).start()

    def __exit__(self,*args):
        if not self.retained:
            self.close()

    def close(self):
        self.closed.set()
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=2)
            except (OSError,ValueError,subprocess.TimeoutExpired):
                if self.proc.poll() is None:
                    self.proc.terminate()
                    self.proc.wait(timeout=2)
