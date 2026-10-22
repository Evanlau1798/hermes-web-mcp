import math
import json
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from compact_state import valid_timestamp, should_compact
from operation_summary import summarize, observe_turn as observe_latest_turn
from codex_failure import error_details
from compact_snapshot import read_snapshot


class CompactJobs:
    MAX_JOBS = 64
    MAX_RECEIPTS = 10000
    ACTIVE = ('awaiting_confirmation', 'confirming', 'compacting', 'sending')
    UUID = re.compile(r'^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$', re.I)
    OPERATION_ID = re.compile(r'^[A-Za-z0-9_-]{8,128}$')
    DELIVERY_STATES = {'accepted', 'unknown', 'not-sent', 'failed', 'rejected'}

    def __init__(self, api, root=None, poll_interval=2.0, deadline_seconds=180,
                 clock=None, sleep=None, recover=True):
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
            columns = {r[1] for r in db.execute('PRAGMA table_info(jobs)')}
            for name in ('execution_backend','backend_instance_id','native_turn_id','baseline_turn_status','error_details'):
                if name not in columns:
                    db.execute(f'ALTER TABLE jobs ADD COLUMN {name} TEXT')
            db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS one_active_compact_job_per_thread
                ON jobs(thread_id) WHERE state IN ('awaiting_confirmation','confirming','compacting','sending')''')
            now = time.time()
            db.execute('''UPDATE jobs SET state='interrupted', error_code='PROCESS_RESTARTED', updated_at=?,
                delivery_state=CASE WHEN delivery_started_at IS NOT NULL THEN 'unknown' ELSE delivery_state END
                WHERE state IN ('awaiting_confirmation','confirming','compacting','sending') AND ?''', (now, int(recover)))

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
        from operation_summary import compact_receipt
        worker = self.jobs.get(row['operation_id'])
        return compact_receipt(row, bool(worker and worker.is_alive()))

    def _read_snapshot(self, thread_id, expected_title, binding=None):
        return read_snapshot(self.api,thread_id,expected_title,self._binding(binding))

    @staticmethod
    def _complete(snapshot):
        return snapshot['turn_status'] == 'completed' and valid_timestamp(snapshot['completed_at'])

    @staticmethod
    def _terminal(snapshot):
        return snapshot['turn_status'] in ('completed','interrupted','failed') and valid_timestamp(snapshot['completed_at'])

    @staticmethod
    def _confirmed_compaction(snapshot):
        return (snapshot['compacted'] and snapshot['compact_turn_id'] == snapshot['turn_id']
                and snapshot['native_compaction_item_seen'] is True
                and snapshot['compact_completed_at'] == snapshot['completed_at']
                and CompactJobs._complete(snapshot))

    @staticmethod
    def _same_baseline(current, row):
        return (current['thread_state'] == 'idle' and CompactJobs._terminal(current)
                and current['turn_id'] == row['baseline_turn_id']
                and current['turn_status'] == (row.get('baseline_turn_status') or 'completed')
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

    @staticmethod
    def _binding(row):
        return {k:row[k] for k in ('execution_backend','backend_instance_id') if row and row.get(k)}

    def prepare(self, thread_id, expected_title, message, operation_id, **binding):
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
        current, error = self._read_snapshot(thread_id, expected_title, binding)
        if error:
            return {**self._error(error),'error_details':(current or {}).get('diagnostic')}
        if current['thread_state'] != 'idle':
            return self._error('THREAD_NOT_IDLE')
        if not self._terminal(current):
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
            db.execute('UPDATE jobs SET execution_backend=?,backend_instance_id=?,baseline_turn_status=? WHERE operation_id=?',
                       (binding.get('execution_backend'),binding.get('backend_instance_id'),current['turn_status'],operation_id))
            return self._view(dict(db.execute('SELECT * FROM jobs WHERE operation_id=?',
                                              (operation_id,)).fetchone()))

    def confirm(self, operation_id, choice='compact'):
        if choice not in ('auto', 'compact', 'send_without_compact', 'cancel'):
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
        current, error = self._read_snapshot(row['thread_id'], payload[0], row)
        if error:
            self._reject(operation_id, error,(current or {}).get('diagnostic'))
            return self._view_for_id(operation_id)
        if not self._same_baseline(current, row):
            self._reject(operation_id, 'BASELINE_CHANGED')
            return self._view_for_id(operation_id)
        if choice == 'auto':
            choice = 'compact' if current['auto_compact'] else 'send_without_compact'
            self._update(operation_id, choice=choice)
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

    def _reject(self, operation_id, code, details=None):
        with self.lock:
            self._update(operation_id, state='rejected', error_code=code, delivery_state='not-sent',
                         error_details=json.dumps(details) if details else None)
            self.payloads.pop(operation_id, None)

    def _interrupt(self, operation_id, code):
        with self.lock:
            row = self._row(operation_id)
            if row and row['state'] in self.ACTIVE:
                self._update(operation_id, state='interrupted', error_code=code,
                             delivery_state='unknown' if row['delivery_started_at'] else 'not-sent')
            self.payloads.pop(operation_id, None)

    def _fail_compaction(self, operation_id, state, code, details=None):
        with self.lock:
            self._update(operation_id, state=state, error_code=code, delivery_state='not-sent',
                         error_details=json.dumps(details) if details else None)
            self.payloads.pop(operation_id, None)

    def _compact(self, operation_id, row, title):
        if self.closed:
            self._interrupt(operation_id, 'CLOSED')
            return None
        current, error = self._read_snapshot(row['thread_id'], title, row)
        if error:
            self._fail_compaction(operation_id, 'compaction_failed', error,(current or {}).get('diagnostic'))
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
                                                'expected_baseline_turn_id': row['baseline_turn_id'], **self._binding(row)})
        except Exception as exc:
            result = {'error_details':error_details(exc,'compact_start')}
        delivery = result.get('deliveryState') if isinstance(result, dict) else None
        if delivery != 'accepted':
            state = 'compaction_failed' if delivery in ('not-sent', 'failed', 'rejected') else 'compaction_unknown'
            self._fail_compaction(operation_id, state,
                                  'COMPACTION_START_REJECTED' if state == 'compaction_failed'
                                  else 'COMPACTION_START_UNKNOWN',
                                  (result.get('error_details') if isinstance(result,dict) else None) or error_details(result,'compact_start'))
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
                current, error = self._read_snapshot(row['thread_id'], title, row)
            except Exception as exc:
                current, error = {'diagnostic':error_details(exc,'compact_poll')}, 'STATUS_UNAVAILABLE'
            if error:
                self._fail_compaction(operation_id, 'compaction_unknown', 'COMPACTION_STATUS_UNKNOWN',(current or {}).get('diagnostic'))
                return None
            if current['turn_id'] != row['baseline_turn_id'] and current['turn_status'] in ('failed', 'interrupted'):
                self._fail_compaction(operation_id, 'compaction_failed', 'COMPACTION_FAILED',current.get('diagnostic'))
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
        verified, error = self._read_snapshot(row['thread_id'], title, row)
        if error or verified['thread_state'] != 'idle' or verified['turn_id'] != candidate['turn_id'] \
                or verified['turn_status'] != 'completed' or verified['completed_at'] is None \
                or not self._confirmed_compaction(verified) or verified['previous_turn_id'] != row['baseline_turn_id']:
            self._fail_compaction(operation_id, 'compaction_failed', error or 'COMPACTION_NOT_CONFIRMED',
                                  (verified or {}).get('diagnostic'))
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
                'operation_id': row['delivery_operation_id'], 'expected_baseline_turn_id': baseline, **self._binding(row)}
        if row['choice'] == 'send_without_compact' or row['baseline_compacted']:
            args['skip_compaction'] = True
        try:
            result = self.api('send', args)
        except Exception:
            result = None
        state = result.get('deliveryState') if isinstance(result, dict) else None
        if isinstance(result,dict) and result.get('native_turn_id'):
            self._update(operation_id,native_turn_id=result['native_turn_id'])
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
                current, error = self._read_snapshot(row['thread_id'], title, row)
                if error:
                    self._fail_compaction(operation_id, 'delivery_not_sent', error)
                    return
                same = self._same_baseline(current, row)
                if not same:
                    self._fail_compaction(operation_id, 'delivery_not_sent', 'BASELINE_CHANGED')
                    return
            self._send(operation_id, row, title, message, baseline)
        except Exception as exc:
            row = self._row(operation_id)
            if row and row['delivery_started_at']:
                self._update(operation_id, state='delivery_unknown', delivery_state='unknown', error_code='WORKER_FAILED',
                             error_details=json.dumps(error_details(exc,'worker')))
            else:
                self._fail_compaction(operation_id, 'compaction_unknown', 'WORKER_FAILED',error_details(exc,'worker'))

    def status(self, operation_id, observe_turn=False):
        if not isinstance(observe_turn, bool):
            raise ValueError('observe_turn must be boolean')
        with self.lock:
            row = self._row(operation_id)
            result = self._view(row) if row else self._error('OPERATION_NOT_FOUND')
        if observe_turn and row:
            result['turn_observation'] = observe_latest_turn(self.api, row)
            result['summary']['turn'] = result['turn_observation']['state']
        return result

    def close(self, timeout=1.0):
        until = time.monotonic() + max(0.0, timeout)
        with self.lock:
            if not self.closed:
                self.closed = True
                with self._db() as db:
                    db.executemany('''UPDATE jobs SET state='interrupted',error_code='CLOSED',updated_at=?
                        WHERE operation_id=? AND state IN ('awaiting_confirmation','confirming')''',
                        [(time.time(),key) for key in self.payloads])
                for operation_id in list(self.payloads):
                    row = self._row(operation_id)
                    if row and row['state'] == 'interrupted':
                        self.payloads.pop(operation_id, None)
            workers = list(self.jobs.values())
        for worker in workers:
            if worker is not threading.current_thread():
                worker.join(max(0.0, until - time.monotonic()))
        return {'still_running': sum(worker.is_alive() for worker in workers)}
