"""Process-local, read-only tracking for Codex sessions and Computer Use."""

from collections import OrderedDict, deque
import copy
import hashlib
import json
import math
import re
import threading
import time
import uuid


class _SampleFailure(Exception):
    def __init__(self, stage, error_code):
        self.stage = stage
        self.error_code = error_code


class TrackingManager:
    """Own bounded worker threads and events; tracking IDs are process-local bearer IDs."""

    MAX_TRACKERS = 64
    MAX_EVENTS = 64
    MAX_SNAPSHOT_BYTES = 256 * 1024
    MIN_INTERVAL = 5.0
    MAX_INTERVAL = 3600.0
    MAX_DEADLINE = 86400.0
    MAX_JOIN_TIMEOUT = 30.0

    def __init__(self, *, clock=time.monotonic, waiter=None):
        if not callable(clock) or (waiter is not None and not callable(waiter)):
            raise TypeError('clock and waiter must be callable')
        self._clock = clock
        self._waiter = waiter or (lambda stop_event, timeout: stop_event.wait(timeout))
        self._lock = threading.RLock()
        self._computer_lock = threading.Lock()
        self._trackers = OrderedDict()
        self._closed = False

    def start(self, kind, session_id, sampler, *, mode='manual', interval=30,
              deadline_seconds=None, expected_turn_id=None, completion_predicate=None):
        """Start a poller. ``sampler`` is a bounded, read-only zero-argument callback."""
        if kind not in ('codex', 'computer'):
            raise ValueError('kind must be codex or computer')
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id) > 256:
            raise ValueError('session_id must be a nonempty string of at most 256 characters')
        if kind == 'codex':
            try:
                uuid.UUID(session_id)
            except (ValueError, AttributeError) as exc:
                raise ValueError('Codex session_id must be a thread UUID') from exc
        if not callable(sampler):
            raise TypeError('sampler must be callable')
        if mode not in ('manual', 'task_complete'):
            raise ValueError('mode must be manual or task_complete')
        interval = self._bounded_number(interval, self.MIN_INTERVAL, self.MAX_INTERVAL, 'interval')
        if deadline_seconds is not None:
            deadline_seconds = self._bounded_number(deadline_seconds, 0.001, self.MAX_DEADLINE,
                                                    'deadline_seconds')
        if kind == 'codex':
            if completion_predicate is not None:
                raise ValueError('Codex completion uses turn status, not a predicate')
            if expected_turn_id is not None:
                if mode != 'task_complete' or not isinstance(expected_turn_id, str) \
                        or not expected_turn_id or len(expected_turn_id) > 256:
                    raise ValueError('expected_turn_id requires task_complete and a nonempty turn ID')
            predicate = None
        else:
            if expected_turn_id is not None:
                raise ValueError('expected_turn_id is only valid for Codex')
            predicate = self._validate_predicate(completion_predicate) if mode == 'task_complete' else None
            if mode == 'manual' and completion_predicate is not None:
                raise ValueError('completion_predicate requires task_complete mode')

        now = self._clock()
        tracking_id = uuid.uuid4().hex
        entry = {
            'tracking_id': tracking_id, 'kind': kind, 'session_id': session_id,
            'sampler': sampler, 'mode': mode, 'interval': interval,
            'deadline_at': now + deadline_seconds if deadline_seconds is not None else None,
            'expected_turn_id': expected_turn_id, 'predicate': predicate,
            'stop_event': threading.Event(), 'stop_reason': 'stopped', 'state': 'running',
            'call_lock': threading.Lock(),
            'terminal_reason': None, 'started_at': now, 'heartbeat_at': now,
            'last_success_monotonic': None, 'current_state': None,
            'sample_count': 0, 'error_count': 0, 'completed_samples': 0,
            'consecutive_errors': 0, 'degraded': False, 'last_error': None,
            'fingerprint': None, 'candidate_turn_id': None,
            'events': deque(), 'event_sequence': 0, 'events_dropped': 0,
            'thread': None, 'condition': threading.Condition(self._lock),
        }
        with self._lock:
            if self._closed:
                raise RuntimeError('tracking manager is closed')
            if len(self._trackers) >= self.MAX_TRACKERS:
                for old_id, old in self._trackers.items():
                    if old['state'] == 'terminal':
                        del self._trackers[old_id]
                        break
                else:
                    raise RuntimeError('tracking capacity reached')
            worker = threading.Thread(target=self._run, args=(entry,),
                                      name='mcp-tracking-' + tracking_id[:8], daemon=True)
            entry['thread'] = worker
            self._trackers[tracking_id] = entry
            try:
                worker.start()
            except BaseException:
                del self._trackers[tracking_id]
                raise
            return self._status_locked(entry, 0, self.MAX_EVENTS)

    def status(self, tracking_id, *, after_sequence=0, limit=64, wait_seconds=0):
        if isinstance(after_sequence, bool) or not isinstance(after_sequence, int) or after_sequence < 0:
            raise ValueError('after_sequence must be a nonnegative integer')
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self.MAX_EVENTS:
            raise ValueError('limit must be between 1 and MAX_EVENTS')
        wait_seconds = self._bounded_number(wait_seconds, 0.0, 25.0, 'wait_seconds')
        started = time.monotonic()
        with self._lock:
            entry = self._get_locked(tracking_id)
            completed = entry['completed_samples']
            reason = 'terminal' if entry['state'] == 'terminal' else 'immediate'
            if wait_seconds and reason != 'terminal' \
                    and entry['event_sequence'] <= after_sequence:
                while True:
                    remaining = wait_seconds - (time.monotonic() - started)
                    if remaining <= 0:
                        reason = 'timeout'
                        break
                    entry['condition'].wait(remaining)
                    if entry['state'] == 'terminal':
                        reason = 'terminal'
                    elif entry['event_sequence'] > after_sequence:
                        reason = 'events'
                    elif entry['completed_samples'] != completed:
                        reason = 'heartbeat'
                    else:
                        continue
                    break
            result = self._status_locked(entry, after_sequence, limit)
            result['wait'] = {'reason': reason, 'requested_seconds': wait_seconds,
                              'elapsed_seconds': time.monotonic() - started}
            return result

    def stop(self, tracking_id, *, timeout=5.0):
        timeout = self._bounded_number(timeout, 0.0, self.MAX_JOIN_TIMEOUT, 'timeout')
        end = time.monotonic() + timeout
        with self._lock:
            entry = self._get_locked(tracking_id)
            self._request_stop_locked(entry, 'stopped')
            worker = entry['thread']
            call_lock = entry['call_lock']
        acquired = call_lock.acquire(timeout=max(0.0, end - time.monotonic()))
        if acquired:
            call_lock.release()
        if worker is not threading.current_thread():
            worker.join(max(0.0, end - time.monotonic()))
        return self.status(tracking_id)

    def close(self, *, timeout=5.0):
        """Stop all workers and join them within one shared bounded timeout."""
        timeout = self._bounded_number(timeout, 0.0, self.MAX_JOIN_TIMEOUT, 'timeout')
        with self._lock:
            self._closed = True
            entries = list(self._trackers.values())
            for entry in entries:
                self._request_stop_locked(entry, 'manager_closed')
        end = time.monotonic() + timeout
        for entry in entries:
            worker = entry['thread']
            if worker is not threading.current_thread():
                worker.join(max(0.0, end - time.monotonic()))
        return {'joined': sum(not entry['thread'].is_alive() for entry in entries),
                'still_running': sum(entry['thread'].is_alive() for entry in entries)}

    def _run(self, entry):
        try:
            while True:
                if entry['stop_event'].is_set():
                    self._finish(entry, entry['stop_reason'])
                    return
                now = self._clock()
                if entry['deadline_at'] is not None and now >= entry['deadline_at']:
                    self._finish(entry, 'deadline')
                    return
                with self._lock:
                    if entry['state'] != 'running':
                        self._finish_locked(entry, entry['stop_reason'])
                        return
                    entry['heartbeat_at'] = now
                    entry['sample_count'] += 1
                snapshot = None
                try:
                    with entry['call_lock']:
                        if entry['stop_event'].is_set():
                            self._finish(entry, entry['stop_reason'])
                            return
                        if entry['kind'] == 'computer':
                            with self._computer_lock:
                                if entry['stop_event'].is_set():
                                    self._finish(entry, entry['stop_reason'])
                                    return
                                snapshot = entry['sampler']()
                        else:
                            snapshot = entry['sampler']()
                    if entry['stop_event'].is_set():
                        self._finish(entry, entry['stop_reason'])
                        return
                    if entry['deadline_at'] is not None and self._clock() >= entry['deadline_at']:
                        self._finish(entry, 'deadline')
                        return
                    view, fingerprint, matched = self._inspect(entry, snapshot)
                except _SampleFailure as exc:
                    if entry['stop_event'].is_set():
                        self._finish(entry, entry['stop_reason'])
                        return
                    if entry['deadline_at'] is not None and self._clock() >= entry['deadline_at']:
                        self._finish(entry, 'deadline')
                        return
                    self._sample_error(entry, exc.stage, exc.error_code)
                    view, fingerprint, matched = None, None, False
                except Exception as exc:
                    if entry['stop_event'].is_set():
                        self._finish(entry, entry['stop_reason'])
                        return
                    if entry['deadline_at'] is not None and self._clock() >= entry['deadline_at']:
                        self._finish(entry, 'deadline')
                        return
                    stage = 'codex_status' if entry['kind'] == 'codex' else 'computer_capture'
                    code = 'timeout' if isinstance(exc, TimeoutError) else getattr(exc, 'error_code', None)
                    self._sample_error(entry, stage, self._safe_code(code, 'sampler_failed'))
                    view, fingerprint, matched = None, None, False
                if view is not None:
                    # Publish sample completion and terminal state atomically to waiting queries.
                    with self._lock:
                        terminal = self._sample_success(entry, view, fingerprint, matched)
                        if terminal:
                            self._finish_locked(entry, terminal)
                            return
                snapshot = None
                remaining = entry['interval']
                if entry['deadline_at'] is not None:
                    remaining = min(remaining, max(0.0, entry['deadline_at'] - self._clock()))
                self._waiter(entry['stop_event'], remaining)
        except BaseException:
            self._finish(entry, 'worker_error')

    def _inspect(self, entry, snapshot):
        try:
            encoded = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(',', ':'),
                                 allow_nan=False).encode('utf-8')
        except (TypeError, ValueError, RecursionError):
            raise _SampleFailure('snapshot_validation', 'invalid_snapshot') from None
        if len(encoded) > self.MAX_SNAPSHOT_BYTES:
            raise _SampleFailure('snapshot_validation', 'snapshot_too_large')
        if not isinstance(snapshot, dict):
            raise _SampleFailure('snapshot_validation', 'invalid_snapshot')
        if snapshot.get('ok') is False or snapshot.get('success') is False \
                or isinstance(snapshot.get('errorCode'), str) or isinstance(snapshot.get('error'), str):
            stage = 'codex_status' if entry['kind'] == 'codex' else 'computer_capture'
            fallback = 'codex_status_failed' if entry['kind'] == 'codex' else 'computer_capture_failed'
            raise _SampleFailure(stage, self._safe_code(snapshot.get('errorCode'), fallback))
        if entry['kind'] == 'computer':
            if self._contains_image(snapshot):
                raise _SampleFailure('computer_capture', 'image_data_rejected')
            digest = hashlib.sha256(encoded).hexdigest()
            summary = snapshot.get('summary')
            summary = summary[:256] if isinstance(summary, str) else None
            return {'fingerprint': digest, 'summary': summary}, digest, \
                self._matches(snapshot, entry['predicate']) if entry['predicate'] else False

        thread = snapshot.get('thread')
        state = thread.get('status') if isinstance(thread, dict) else None
        thread_status = state.get('type') if isinstance(state, dict) else None
        if not isinstance(thread_status, str) or not thread_status or len(thread_status) > 64 \
                or thread.get('id') != entry['session_id'] or thread_status == 'error':
            raise _SampleFailure('codex_status', 'invalid_codex_status')
        latest = snapshot.get('latestTurn')
        if latest is not None and not isinstance(latest, dict):
            raise _SampleFailure('codex_status', 'invalid_latest_turn')
        turn_id = latest.get('id') if latest else None
        turn_status = latest.get('status') if latest else None
        if turn_id is not None and (not isinstance(turn_id, str) or len(turn_id) > 256):
            raise _SampleFailure('codex_status', 'invalid_turn_id')
        if turn_status is not None and (not isinstance(turn_status, str) or len(turn_status) > 64):
            raise _SampleFailure('codex_status', 'invalid_turn_status')
        view = {'thread_status': thread_status,
                'latest_turn': None if latest is None else {'id': turn_id, 'status': turn_status}}
        digest = hashlib.sha256(json.dumps((thread_status, turn_id, turn_status),
                                           separators=(',', ':')).encode()).hexdigest()
        return view, digest, False

    def _sample_success(self, entry, view, fingerprint, matched):
        now = self._clock()
        with self._lock:
            if entry['state'] != 'running' or entry['stop_event'].is_set():
                self._finish_locked(entry, entry['stop_reason'])
                return entry['terminal_reason']
            if entry['degraded']:
                self._event_locked(entry, 'recovered', 'Sampling recovered')
            entry['degraded'] = False
            entry['last_error'] = None
            entry['consecutive_errors'] = 0
            entry['last_success_monotonic'] = now
            entry['current_state'] = view.copy()
            if fingerprint != entry['fingerprint']:
                event_type = 'baseline' if entry['fingerprint'] is None else 'changed'
                entry['fingerprint'] = fingerprint
                self._event_locked(entry, event_type, self._summary(entry['kind'], view, event_type))
            entry['completed_samples'] += 1
            entry['condition'].notify_all()
            if entry['mode'] != 'task_complete':
                return None
            if entry['kind'] == 'computer' and matched:
                return 'task_complete'
            if entry['kind'] == 'codex':
                thread_status = view['thread_status']
                latest = view['latest_turn'] or {}
                turn_id, turn_status = latest.get('id'), latest.get('status')
                if turn_id and thread_status == 'active' and turn_status == 'inProgress':
                    entry['candidate_turn_id'] = turn_id
                expected = entry['expected_turn_id']
                observed = expected is not None and turn_id == expected
                witnessed = turn_id is not None and turn_id == entry['candidate_turn_id']
                if thread_status == 'idle' and turn_status == 'completed' \
                        and (observed or witnessed):
                    return 'task_complete'
                if thread_status == 'idle' and turn_status in ('interrupted', 'failed') \
                        and (observed or witnessed):
                    return 'turn_' + turn_status
        return None

    def _sample_error(self, entry, stage, error_code):
        error = {'stage': stage, 'errorCode': error_code}
        with self._lock:
            if entry['state'] != 'running' or entry['stop_event'].is_set():
                self._finish_locked(entry, entry['stop_reason'])
                return
            entry['error_count'] += 1
            entry['consecutive_errors'] += 1
            if not entry['degraded'] or entry['last_error'] != error:
                self._event_locked(entry, 'sampling_error',
                                   'Sampling failed at ' + stage + ' (' + error_code + ')')
            entry['degraded'] = True
            entry['last_error'] = error
            entry['current_state'] = None
            entry['completed_samples'] += 1
            entry['condition'].notify_all()

    def _finish(self, entry, reason):
        with self._lock:
            self._finish_locked(entry, reason)

    def _finish_locked(self, entry, reason):
        if entry['state'] == 'terminal':
            return
        if entry['state'] == 'stopping':
            reason = entry['stop_reason']
        entry['state'] = 'terminal'
        entry['terminal_reason'] = reason
        entry['finished_at'] = self._clock()
        entry['stop_event'].set()
        self._event_locked(entry, 'terminal', 'Tracking ended (' + reason + ')')
        entry['condition'].notify_all()

    def _request_stop_locked(self, entry, reason):
        if entry['state'] != 'terminal':
            entry['state'] = 'stopping'
            entry['stop_reason'] = reason
            entry['stop_event'].set()

    def _event_locked(self, entry, event_type, summary):
        if len(entry['events']) == self.MAX_EVENTS:
            entry['events_dropped'] += 1
        entry['event_sequence'] += 1
        entry['events'].append({'sequence': entry['event_sequence'],
                                'at_monotonic_seconds': self._clock(),
                                'type': event_type, 'summary': summary[:128]})

    def _status_locked(self, entry, after_sequence, limit):
        events = list(entry['events'])
        oldest = events[0]['sequence'] if events else entry['event_sequence'] + 1
        selected = [event.copy() for event in events if event['sequence'] > after_sequence][:limit]
        now = self._clock()
        return {
            'tracking_id': entry['tracking_id'], 'kind': entry['kind'], 'mode': entry['mode'],
            'state': entry['state'], 'terminal_reason': entry['terminal_reason'],
            'alive': entry['thread'].is_alive(), 'interval_seconds': entry['interval'],
            'deadline_remaining_seconds': None if entry['deadline_at'] is None else
                max(0.0, entry['deadline_at'] - now),
            'heartbeat_monotonic_seconds': entry['heartbeat_at'],
            'heartbeat_age_seconds': max(0.0, now - entry['heartbeat_at']),
            'last_success_monotonic_seconds': entry['last_success_monotonic'],
            'current_state': copy.deepcopy(entry['current_state']),
            'sample_count': entry['sample_count'],
            'error_count': entry['error_count'], 'consecutive_errors': entry['consecutive_errors'],
            'degraded': entry['degraded'],
            'last_error': None if entry['last_error'] is None else entry['last_error'].copy(),
            'latest_sequence': entry['event_sequence'],
            'events_dropped': entry['events_dropped'],
            'missed_events': max(0, oldest - after_sequence - 1), 'events': selected,
        }

    def _get_locked(self, tracking_id):
        if not isinstance(tracking_id, str) or len(tracking_id) != 32:
            raise KeyError('unknown tracking_id')
        try:
            return self._trackers[tracking_id]
        except KeyError as exc:
            raise KeyError('unknown tracking_id') from exc

    @staticmethod
    def _summary(kind, view, event_type):
        if kind == 'computer':
            return 'Computer snapshot observed' if event_type == 'baseline' else 'Computer snapshot changed'
        thread_status = view['thread_status']
        latest = view['latest_turn'] or {}
        turn_status = latest.get('status')
        safe_thread = thread_status if thread_status in ('active', 'idle', 'notLoaded') else 'other'
        safe_turn = turn_status if turn_status in ('inProgress', 'completed', 'interrupted', 'failed') else 'other'
        return 'Codex thread ' + safe_thread + '; latest turn ' + (safe_turn or 'unknown')

    @staticmethod
    def _contains_image(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if str(key).casefold() in {'image', 'images', 'image_url', 'screenshot', '_multimodal'}:
                    return True
                if TrackingManager._contains_image(item):
                    return True
        elif isinstance(value, list):
            return any(TrackingManager._contains_image(item) for item in value)
        elif isinstance(value, str) and value.startswith('data:image/'):
            return True
        return False

    @staticmethod
    def _validate_predicate(predicate):
        if not isinstance(predicate, dict) or set(predicate) not in ({'path', 'contains'}, {'path', 'equals'}):
            raise ValueError('completion_predicate must use path with contains or equals')
        path = predicate['path']
        if not isinstance(path, list) or not 1 <= len(path) <= 8:
            raise ValueError('completion predicate path must contain 1 to 8 keys or indexes')
        for part in path:
            if isinstance(part, bool) or (isinstance(part, int) and part < 0) \
                    or (isinstance(part, str) and (not part or len(part) > 128)) \
                    or not isinstance(part, (str, int)):
                raise ValueError('completion predicate path contains an invalid key or index')
        op = 'contains' if 'contains' in predicate else 'equals'
        literal = predicate[op]
        if op == 'contains':
            if not isinstance(literal, str) or not literal or len(literal) > 256:
                raise ValueError('contains must be a nonempty literal of at most 256 characters')
        elif literal is not None and not isinstance(literal, (str, bool, int, float)):
            raise ValueError('equals must be a JSON scalar literal')
        elif isinstance(literal, float) and not math.isfinite(literal):
            raise ValueError('equals must be finite')
        elif isinstance(literal, str) and len(literal) > 256:
            raise ValueError('equals literal is too long')
        return tuple(path), op, literal

    @staticmethod
    def _matches(snapshot, predicate):
        path, op, literal = predicate
        value = snapshot
        for part in path:
            if isinstance(part, int):
                if not isinstance(value, list) or part >= len(value):
                    return False
                value = value[part]
            else:
                if not isinstance(value, dict) or part not in value:
                    return False
                value = value[part]
        if op == 'contains':
            return isinstance(value, str) and literal in value
        return type(value) is type(literal) and value == literal

    @staticmethod
    def _safe_code(value, fallback):
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,63}', value):
            return value
        return fallback

    @staticmethod
    def _bounded_number(value, minimum, maximum, name):
        if isinstance(value, bool) or not isinstance(value, (int, float)) \
                or not minimum <= value <= maximum or not math.isfinite(value):
            raise ValueError(f'{name} must be between {minimum} and {maximum}')
        return float(value)
