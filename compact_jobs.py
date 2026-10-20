import math
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from compact_state import valid_timestamp


class CompactJobs:
    MAX_JOBS = 64
    MAX_RECEIPTS = 10000
    ACTIVE = ('awaiting_confirmation', 'confirming', 'compacting', 'sending')
    UUID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$', re.I)
    OPERATION_ID = re.compile(r'^[A-Za-z0-9_-]{8,119}$')
    DELIVERY_STATES = {'accepted', 'unknown', 'not-sent', 'failed', 'rejected'}

    def __init__(self, api, root=None, poll_interval=2.0, deadline_seconds=180,
                 clock=None, sleep=None):
        if not callable(api):
            raise ValueError('api must be callable')
        if isinstance(poll_interval, bool) or not isinstance(poll_interval, (int, float)) \
                or not math.isfinite(poll_interval) or poll_interval < 2:
            raise ValueError('poll_interval must be at least 2 seconds')
        if isinstance(deadline_seconds, bool) or not isinstance(deadline_seconds, (int, float)) \
                or not math.isfinite(deadline_seconds) or not 0 < deadline_seconds <= 600:
            raise ValueError('deadline_seconds must be between 0 and 600')
        self.api = api
        self.root = Path(root) if root is not None else Path(__file__).resolve().parent / 'runtime'
        self.path = self.root / 'compact-jobs.sqlite'
        self.poll_interval = float(poll_interval)
        self.deadline_seconds = float(deadline_seconds)
        self.clock = clock or time.monotonic
        self.sleep = sleep or time.sleep
        self.lock = threading.RLock()
        self.jobs = {}
        self.payloads = {}
        self.closed = False
        self.root.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS jobs (
                operation_id TEXT PRIMARY KEY, thread_id TEXT NOT NULL, delivery_operation_id TEXT NOT NULL,
                baseline_turn_id TEXT NOT NULL, baseline_completed_at REAL NOT NULL,
                baseline_compacted INTEGER NOT NULL, baseline_compact_turn_id TEXT,
                baseline_compact_completed_at REAL,
                baseline_native_compaction_item_seen INTEGER, state TEXT NOT NULL, choice TEXT,
                compact_turn_id TEXT, compact_complete INTEGER NOT NULL,
                delivery_state TEXT, error_code TEXT, created_at REAL NOT NULL,
                updated_at REAL NOT NULL, confirmed_at REAL, compact_started_at REAL,
                compact_completed_at REAL, delivery_started_at REAL, delivery_completed_at REAL)''')
            db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS one_active_compact_job_per_thread
                ON jobs(thread_id) WHERE state IN ('awaiting_confirmation','confirming','compacting','sending')''')
            now = time.time()
            db.execute('''UPDATE jobs SET state='interrupted', error_code='PROCESS_RESTARTED', updated_at=?,
                delivery_state=CASE WHEN delivery_started_at IS NOT NULL THEN 'unknown' ELSE delivery_state END
                WHERE state IN ('awaiting_confirmation','confirming','compacting','sending')''', (now,))

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=2)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _error(code, state='rejected'):
        return {'ok': False, 'state': state, 'errorCode': code, 'delivery_state': 'not-sent',
                'compact_complete': False, 'worker_alive': False}

    def _row(self, operation_id):
        with self._db() as db:
            row = db.execute('SELECT * FROM jobs WHERE operation_id=?', (operation_id,)).fetchone()
        return dict(row) if row else None

    def _update(self, operation_id, **values):
        values['updated_at'] = time.time()
        columns = ','.join(f'{key}=?' for key in values)
        with self._db() as db:
            cursor = db.execute(f'UPDATE jobs SET {columns} WHERE operation_id=?',
                                (*values.values(), operation_id))
            return cursor.rowcount == 1

    def _view(self, row):
        worker = self.jobs.get(row['operation_id'])
        complete = bool(row['compact_complete'])
        compact_state = {'turnId': row['compact_turn_id'] if complete else row['baseline_compact_turn_id'],
                         'completedAt': row['compact_completed_at'] if complete else row['baseline_compact_completed_at'],
                         'lastTurnIsCompaction': complete or bool(row['baseline_compacted']),
                         'nativeCompactionItemSeen': True if complete else row['baseline_native_compaction_item_seen']}
        return {'ok': row['state'] not in ('rejected', 'canceled', 'interrupted', 'compaction_failed',
                                           'compaction_unknown', 'compaction_timeout', 'delivery_unknown',
                                           'delivery_not_sent', 'delivery_failed'),
                'operation_id': row['operation_id'], 'thread_id': row['thread_id'],
                'delivery_operation_id': row['delivery_operation_id'],
                'baseline_turn_id': row['baseline_turn_id'], 'compact_turn_id': row['compact_turn_id'],
                'baseline_completed_at': row['baseline_completed_at'],
                'baseline_compacted': bool(row['baseline_compacted']), 'compactState': compact_state,
                'state': row['state'], 'worker_alive': bool(worker and worker.is_alive()),
                'compact_complete': bool(row['compact_complete']), 'delivery_state': row['delivery_state'],
                'reconfirmation_required': row['state'] == 'interrupted',
                'errorCode': row['error_code'], 'created_at': row['created_at'],
                'updated_at': row['updated_at'], 'confirmed_at': row['confirmed_at'],
                'compact_started_at': row['compact_started_at'],
                'compact_completed_at': row['compact_completed_at'],
                'delivery_started_at': row['delivery_started_at'],
                'delivery_completed_at': row['delivery_completed_at']}

    def _read_snapshot(self, thread_id, expected_title):
        try:
            data = self.api('status', {'thread_id': thread_id})
        except Exception:
            return None, 'STATUS_UNAVAILABLE'
        if not isinstance(data, dict) or data.get('ok') is False or data.get('errorCode'):
            return None, 'STATUS_UNAVAILABLE'
        thread = data.get('thread')
        status = thread.get('status') if isinstance(thread, dict) else None
        turn = data.get('latestTurn')
        compact = data.get('compactState')
        if not isinstance(thread, dict) or not isinstance(status, dict) or not isinstance(status.get('type'), str) \
                or not isinstance(turn, dict) or not isinstance(turn.get('id'), str) or not turn['id'] \
                or not isinstance(turn.get('status'), str) or not isinstance(compact, dict) \
                or not isinstance(compact.get('lastTurnIsCompaction'), bool):
            return None, 'SNAPSHOT_INVALID'
        if thread.get('id') != thread_id:
            return None, 'THREAD_ID_MISMATCH'
        if thread.get('title') != expected_title:
            return None, 'THREAD_TITLE_MISMATCH'
        if status['type'] == 'notLoaded':
            return None, 'THREAD_NOT_LOADED'
        completed_at = turn.get('completedAt')
        if completed_at is not None and not valid_timestamp(completed_at):
            return None, 'SNAPSHOT_INVALID'
        compact_completed_at = compact.get('completedAt')
        if compact_completed_at is not None and not valid_timestamp(compact_completed_at):
            return None, 'SNAPSHOT_INVALID'
        if compact_completed_at is not None and compact_completed_at != completed_at:
            return None, 'SNAPSHOT_INVALID'
        if compact.get('turnId') is not None and not isinstance(compact.get('turnId'), str):
            return None, 'SNAPSHOT_INVALID'
        seen = compact.get('nativeCompactionItemSeen')
        if seen is not None and not isinstance(seen, bool):
            return None, 'SNAPSHOT_INVALID'
        if not isinstance(turn['id'], str) or len(turn['id']) > 256 \
                or (compact.get('turnId') is not None and len(compact['turnId']) > 256):
            return None, 'SNAPSHOT_INVALID'
        recent = data.get('recentTurnIds')
        previous = recent[1] if isinstance(recent, list) and len(recent) == 2 and recent[0] == turn['id'] else None
        return {'thread_state': status['type'], 'turn_id': turn['id'], 'turn_status': turn['status'], 'previous_turn_id': previous,
                'completed_at': completed_at, 'compacted': compact['lastTurnIsCompaction'],
                'compact_turn_id': compact.get('turnId'),
                'compact_completed_at': compact_completed_at,
                'native_compaction_item_seen': compact.get('nativeCompactionItemSeen')}, None

    @staticmethod
    def _complete(snapshot):
        return snapshot['turn_status'] == 'completed' and valid_timestamp(snapshot['completed_at'])

    @staticmethod
    def _confirmed_compaction(snapshot):
        return (snapshot['compacted'] and snapshot['compact_turn_id'] == snapshot['turn_id']
                and snapshot['native_compaction_item_seen'] is True
                and snapshot['compact_completed_at'] == snapshot['completed_at']
                and CompactJobs._complete(snapshot))

    @staticmethod
    def _same_baseline(current, row):
        return (current['thread_state'] == 'idle' and CompactJobs._complete(current)
                and current['turn_id'] == row['baseline_turn_id']
                and current['turn_status'] == 'completed'
                and current['completed_at'] == row['baseline_completed_at']
                and int(CompactJobs._confirmed_compaction(current)) == row['baseline_compacted']
                and current['compact_turn_id'] == row['baseline_compact_turn_id']
                and current['compact_completed_at'] == row['baseline_compact_completed_at']
                and current['native_compaction_item_seen'] == row['baseline_native_compaction_item_seen'])

    def _view_for_id(self, operation_id):
        row = self._row(operation_id)
        return self._view(row) if row else self._error('OPERATION_NOT_FOUND')

    def _capacity_error(self, db):
        active = db.execute('SELECT count(*) FROM jobs WHERE state IN (?,?,?,?)',
                            self.ACTIVE).fetchone()[0]
        if active >= self.MAX_JOBS:
            return 'JOB_CAPACITY'
        if db.execute('SELECT count(*) FROM jobs').fetchone()[0] >= self.MAX_RECEIPTS:
            return 'RECEIPT_CAPACITY'
        return None

    def prepare(self, thread_id, expected_title, message, operation_id):
        if not isinstance(thread_id, str) or not self.UUID.fullmatch(thread_id) \
                or not isinstance(expected_title, str) or not expected_title or len(expected_title) > 65536 \
                or not isinstance(message, str) or not message.strip() or len(message) > 65536 \
                or not isinstance(operation_id, str) or not self.OPERATION_ID.fullmatch(operation_id):
            return self._error('INVALID_ARGUMENT')
        try:
            if len(message.encode('utf-8')) > 65536:
                return self._error('INVALID_ARGUMENT')
            expected_title.encode('utf-8')
        except UnicodeEncodeError:
            return self._error('INVALID_ARGUMENT')
        with self.lock:
            self.jobs = {key: worker for key, worker in self.jobs.items() if worker.is_alive()}
            existing = self._row(operation_id)
            if existing:
                return self._view(existing) if existing['thread_id'] == thread_id else self._error('OPERATION_ID_CONFLICT')
            if self.closed:
                return self._error('CLOSED')
            with self._db() as db:
                if db.execute('SELECT 1 FROM jobs WHERE thread_id=? AND state IN (?,?,?,?)',
                              (thread_id, *self.ACTIVE)).fetchone():
                    return self._error('SESSION_JOB_EXISTS')
                capacity_error = self._capacity_error(db)
                if capacity_error:
                    return self._error(capacity_error)
        current, error = self._read_snapshot(thread_id, expected_title)
        if error:
            return self._error(error)
        if current['thread_state'] != 'idle':
            return self._error('THREAD_NOT_IDLE')
        if not self._complete(current):
            return self._error('SNAPSHOT_INVALID')
        if current['compacted'] and not self._confirmed_compaction(current):
            return self._error('SNAPSHOT_INVALID')
        now = time.time()
        with self.lock, self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            existing = db.execute('SELECT * FROM jobs WHERE operation_id=?', (operation_id,)).fetchone()
            if existing:
                return self._view(dict(existing)) if existing['thread_id'] == thread_id else self._error('OPERATION_ID_CONFLICT')
            if self.closed:
                return self._error('CLOSED')
            if db.execute('SELECT 1 FROM jobs WHERE thread_id=? AND state IN (?,?,?,?)',
                          (thread_id, *self.ACTIVE)).fetchone():
                return self._error('SESSION_JOB_EXISTS')
            capacity_error = self._capacity_error(db)
            if capacity_error:
                return self._error(capacity_error)
            compacted = int(self._confirmed_compaction(current))
            db.execute('''INSERT INTO jobs(operation_id,thread_id,delivery_operation_id,baseline_turn_id,baseline_completed_at,
                baseline_compacted,baseline_compact_turn_id,baseline_compact_completed_at,
                baseline_native_compaction_item_seen,
                state,choice,compact_turn_id,compact_complete,delivery_state,error_code,
                created_at,updated_at,compact_completed_at)
                VALUES(?,?,?,?,?,?,?,?,?,'awaiting_confirmation',NULL,?,?,NULL,NULL,?,?,?)''',
                (operation_id, thread_id, 'compact-delivery-' + uuid.uuid4().hex, current['turn_id'], current['completed_at'], compacted,
                 current['compact_turn_id'], current['compact_completed_at'],
                 current['native_compaction_item_seen'],
                 current['turn_id'] if compacted else None, compacted, now, now,
                 current['completed_at'] if compacted else None))
            self.payloads[operation_id] = (expected_title, message)
            return self._view(dict(db.execute('SELECT * FROM jobs WHERE operation_id=?',
                                              (operation_id,)).fetchone()))

    def confirm(self, operation_id, choice='compact'):
        if choice not in ('compact', 'send_without_compact', 'cancel'):
            return self._error('INVALID_CHOICE')
        with self.lock:
            row = self._row(operation_id)
            if not row:
                return self._error('OPERATION_NOT_FOUND')
            if row['state'] != 'awaiting_confirmation':
                return self._view(row)
            if choice == 'cancel':
                self._update(operation_id, state='canceled', choice=choice)
                self.payloads.pop(operation_id, None)
                return self._view_for_id(operation_id)
            if self.closed:
                self._update(operation_id, state='interrupted', error_code='CLOSED')
                self.payloads.pop(operation_id, None)
                return self._view_for_id(operation_id)
            payload = self.payloads.get(operation_id)
            if not payload:
                self._update(operation_id, state='interrupted', error_code='RECONFIRMATION_REQUIRED')
                return self._view_for_id(operation_id)
            self._update(operation_id, state='confirming', choice=choice)
        current, error = self._read_snapshot(row['thread_id'], payload[0])
        if error:
            self._reject(operation_id, error)
            return self._view_for_id(operation_id)
        if not self._same_baseline(current, row):
            self._reject(operation_id, 'BASELINE_CHANGED')
            return self._view_for_id(operation_id)
        with self.lock:
            current_row = self._row(operation_id)
            if self.closed or current_row['state'] != 'confirming':
                if current_row['state'] == 'confirming':
                    self._update(operation_id, state='interrupted', error_code='CLOSED')
                    self.payloads.pop(operation_id, None)
                return self._view_for_id(operation_id)
            state = 'compacting' if choice == 'compact' and not row['baseline_compacted'] else 'sending'
            self._update(operation_id, state=state, confirmed_at=time.time())
            worker = threading.Thread(target=self._run, args=(operation_id,), daemon=True,
                                      name='compact-job-' + operation_id)
            self.jobs[operation_id] = worker
            try:
                worker.start()
            except RuntimeError:
                self._update(operation_id, state='compaction_failed', error_code='WORKER_START_FAILED')
                self.payloads.pop(operation_id, None)
            return self._view_for_id(operation_id)

    def _reject(self, operation_id, code):
        with self.lock:
            self._update(operation_id, state='rejected', error_code=code, delivery_state='not-sent')
            self.payloads.pop(operation_id, None)

    def _interrupt(self, operation_id, code):
        with self.lock:
            row = self._row(operation_id)
            if row and row['state'] in self.ACTIVE:
                self._update(operation_id, state='interrupted', error_code=code,
                             delivery_state='unknown' if row['delivery_started_at'] else 'not-sent')
            self.payloads.pop(operation_id, None)

    def _fail_compaction(self, operation_id, state, code):
        with self.lock:
            self._update(operation_id, state=state, error_code=code, delivery_state='not-sent')
            self.payloads.pop(operation_id, None)

    def _compact(self, operation_id, row, title):
        if self.closed:
            self._interrupt(operation_id, 'CLOSED')
            return None
        current, error = self._read_snapshot(row['thread_id'], title)
        if error:
            self._fail_compaction(operation_id, 'compaction_failed', error)
            return None
        if not self._same_baseline(current, row):
            self._fail_compaction(operation_id, 'compaction_failed', 'BASELINE_CHANGED')
            return None
        started = self.clock()
        with self.lock:
            if self.closed:
                self._interrupt(operation_id, 'CLOSED')
                return None
            if not self._update(operation_id, compact_started_at=time.time()):
                self._fail_compaction(operation_id, 'compaction_failed', 'STORE_UNAVAILABLE')
                return None
        try:
            result = self.api('compact_start', {'thread_id': row['thread_id'], 'expected_title': title,
                                                'expected_baseline_turn_id': row['baseline_turn_id']})
        except Exception:
            result = None
        delivery = result.get('deliveryState') if isinstance(result, dict) else None
        if delivery != 'accepted':
            state = 'compaction_failed' if delivery in ('not-sent', 'failed', 'rejected') else 'compaction_unknown'
            self._fail_compaction(operation_id, state,
                                  'COMPACTION_START_REJECTED' if state == 'compaction_failed'
                                  else 'COMPACTION_START_UNKNOWN')
            return None
        deadline = started + self.deadline_seconds
        while True:
            if self.closed:
                self._interrupt(operation_id, 'CLOSED')
                return None
            if self.clock() >= deadline:
                self._fail_compaction(operation_id, 'compaction_timeout', 'COMPACTION_TIMEOUT')
                return None
            try:
                self.sleep(self.poll_interval)
                current, error = self._read_snapshot(row['thread_id'], title)
            except Exception:
                current, error = None, 'STATUS_UNAVAILABLE'
            if error:
                self._fail_compaction(operation_id, 'compaction_unknown', 'COMPACTION_STATUS_UNKNOWN')
                return None
            if current['turn_id'] != row['baseline_turn_id'] and current['turn_status'] in ('failed', 'interrupted'):
                self._fail_compaction(operation_id, 'compaction_failed', 'COMPACTION_FAILED')
                return None
            if current['turn_id'] != row['baseline_turn_id'] and current['turn_status'] == 'completed':
                if not self._confirmed_compaction(current) or current['previous_turn_id'] != row['baseline_turn_id']:
                    self._fail_compaction(operation_id, 'compaction_failed', 'COMPACTION_NOT_CONFIRMED')
                    return None
                candidate = current
                break
            if current['thread_state'] not in ('idle', 'active', 'compacting', 'busy'):
                self._fail_compaction(operation_id, 'compaction_unknown', 'SNAPSHOT_INVALID')
                return None
        verified, error = self._read_snapshot(row['thread_id'], title)
        if error or verified['thread_state'] != 'idle' or verified['turn_id'] != candidate['turn_id'] \
                or verified['turn_status'] != 'completed' or verified['completed_at'] is None \
                or not self._confirmed_compaction(verified) or verified['previous_turn_id'] != row['baseline_turn_id']:
            self._fail_compaction(operation_id, 'compaction_failed', error or 'COMPACTION_NOT_CONFIRMED')
            return None
        with self.lock:
            self._update(operation_id, compact_turn_id=verified['turn_id'], compact_complete=1,
                         compact_completed_at=verified['completed_at'], state='sending')
        return verified['turn_id']

    def _send(self, operation_id, row, title, message, baseline):
        with self.lock:
            if self.closed:
                self._interrupt(operation_id, 'CLOSED')
                return
            if not self._update(operation_id, state='sending', delivery_started_at=time.time()):
                self._fail_compaction(operation_id, 'delivery_not_sent', 'STORE_UNAVAILABLE')
                return
        args = {'thread_id': row['thread_id'], 'expected_title': title, 'message': message,
                'operation_id': row['delivery_operation_id'], 'expected_baseline_turn_id': baseline}
        if row['choice'] == 'send_without_compact' or row['baseline_compacted']:
            args['skip_compaction'] = True
        try:
            result = self.api('send', args)
        except Exception:
            result = None
        state = result.get('deliveryState') if isinstance(result, dict) else None
        reused = isinstance(result, dict) and bool(result.get('deduplicated'))
        if reused:
            state = 'unknown'
        if state not in self.DELIVERY_STATES:
            state = 'unknown'
        final = {'accepted': 'delivery_accepted', 'unknown': 'delivery_unknown',
                 'not-sent': 'delivery_not_sent', 'failed': 'delivery_failed',
                 'rejected': 'delivery_failed'}[state]
        error = {'unknown': 'DELIVERY_UNKNOWN', 'not-sent': 'DELIVERY_NOT_SENT',
                 'failed': 'DELIVERY_FAILED', 'rejected': 'DELIVERY_FAILED'}.get(state)
        if reused:
            error = 'DELIVERY_ID_REUSED'
        with self.lock:
            self._update(operation_id, state=final, delivery_state=state, error_code=error,
                         delivery_completed_at=time.time())
            self.payloads.pop(operation_id, None)

    def _run(self, operation_id):
        try:
            with self.lock:
                row = self._row(operation_id)
                payload = self.payloads.get(operation_id)
                closed = self.closed
            if not row or not payload:
                self._interrupt(operation_id, 'RECONFIRMATION_REQUIRED')
                return
            if closed:
                self._interrupt(operation_id, 'CLOSED')
                return
            title, message = payload
            if row['state'] == 'compacting':
                baseline = self._compact(operation_id, row, title)
                if baseline is None:
                    return
            else:
                baseline = row['baseline_turn_id']
                current, error = self._read_snapshot(row['thread_id'], title)
                if error:
                    self._fail_compaction(operation_id, 'delivery_not_sent', error)
                    return
                same = self._same_baseline(current, row)
                if not same:
                    self._fail_compaction(operation_id, 'delivery_not_sent', 'BASELINE_CHANGED')
                    return
            self._send(operation_id, row, title, message, baseline)
        except Exception:
            row = self._row(operation_id)
            if row and row['delivery_started_at']:
                self._update(operation_id, state='delivery_unknown', delivery_state='unknown', error_code='WORKER_FAILED')
            else:
                self._fail_compaction(operation_id, 'compaction_unknown', 'WORKER_FAILED')

    def status(self, operation_id):
        with self.lock:
            row = self._row(operation_id)
            return self._view(row) if row else self._error('OPERATION_NOT_FOUND')

    def close(self, timeout=1.0):
        until = time.monotonic() + max(0.0, timeout)
        with self.lock:
            if not self.closed:
                self.closed = True
                with self._db() as db:
                    db.execute('''UPDATE jobs SET state='interrupted',error_code='CLOSED',updated_at=?
                        WHERE state IN ('awaiting_confirmation','confirming')''', (time.time(),))
                for operation_id in list(self.payloads):
                    row = self._row(operation_id)
                    if row and row['state'] == 'interrupted':
                        self.payloads.pop(operation_id, None)
            workers = list(self.jobs.values())
        for worker in workers:
            if worker is not threading.current_thread():
                worker.join(max(0.0, until - time.monotonic()))
        return {'still_running': sum(worker.is_alive() for worker in workers)}
