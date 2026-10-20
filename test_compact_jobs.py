import copy
import hashlib
import tempfile
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from compact_jobs import CompactJobs


THREAD_ID = '11111111-1111-4111-8111-111111111111'


def snapshot(thread_id=THREAD_ID, title='Exact title', turn_id='turn0', thread_state='idle',
             turn_state='completed', completed_at=123.0, compacted=False):
    return {'thread': {'id': thread_id, 'title': title, 'status': {'type': thread_state}},
            'recentTurnIds': [turn_id, 'turn0'] if turn_id != 'turn0' else ['turn0'],
            'latestTurn': {'id': turn_id, 'status': turn_state, 'completedAt': completed_at},
            'compactState': {'turnId': turn_id, 'completedAt': completed_at,
                             'lastTurnIsCompaction': compacted,
                             'nativeCompactionItemSeen': compacted}}


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class FakeAPI:
    def __init__(self, statuses=(), start=None, send=None):
        self.statuses = list(statuses)
        self.start = start or {'deliveryState': 'accepted'}
        self.send = send or {'deliveryState': 'accepted'}
        self.calls = []

    def __call__(self, action, arguments):
        self.calls.append((action, dict(arguments)))
        if action == 'status':
            return copy.deepcopy(self.statuses.pop(0) if self.statuses else snapshot())
        if action == 'compact_start':
            return dict(self.start)
        if action == 'send':
            return dict(self.send)
        raise AssertionError(action)


def wait_for(predicate, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        threading.Event().wait(0.002)
    return predicate()


class CompactJobsTests(unittest.TestCase):
    def manager(self, api, folder, **kwargs):
        return CompactJobs(api, root=folder, **kwargs)

    def test_prepare_keeps_payload_in_memory_and_duplicate_does_not_replace_it(self):
        with tempfile.TemporaryDirectory() as folder:
            api = FakeAPI([snapshot()])
            manager = self.manager(api, folder)
            result = manager.prepare(THREAD_ID, 'Exact title', 'private message', 'operation-0001')
            self.assertEqual(result['state'], 'awaiting_confirmation')
            self.assertEqual(result['baseline_turn_id'], 'turn0')
            self.assertEqual(result['baseline_completed_at'], 123.0)
            self.assertFalse(result['baseline_compacted'])
            self.assertEqual(result['compactState']['turnId'], 'turn0')
            self.assertEqual(result['compactState']['completedAt'], 123.0)
            self.assertFalse(result['compactState']['lastTurnIsCompaction'])
            self.assertFalse(result['compactState']['nativeCompactionItemSeen'])
            self.assertTrue(result['delivery_operation_id'].startswith('compact-delivery-'))
            self.assertNotIn('private message', str(result))
            calls = len(api.calls)
            duplicate = manager.prepare(THREAD_ID, 'replacement title', 'replacement message', 'operation-0001')
            self.assertEqual(duplicate['state'], 'awaiting_confirmation')
            self.assertEqual(len(api.calls), calls)
            raw = Path(folder, 'compact-jobs.sqlite').read_bytes()
            for secret in ('private message', 'Exact title', 'replacement message'):
                self.assertNotIn(secret.encode(), raw)
                self.assertNotIn(hashlib.sha256(secret.encode()).hexdigest().encode(), raw)

    def test_prepare_rejects_unloaded_busy_and_incomplete_native_snapshots(self):
        for data, code in ((snapshot(thread_state='notLoaded'), 'THREAD_NOT_LOADED'),
                           (snapshot(thread_state='active'), 'THREAD_NOT_IDLE'),
                           (snapshot(turn_state='inProgress'), 'SNAPSHOT_INVALID'),
                           (snapshot(completed_at=None), 'SNAPSHOT_INVALID'),
                           ({'thread': {'id': THREAD_ID, 'title': 'Exact title', 'status': {'type': 'idle'}}},
                            'SNAPSHOT_INVALID')):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as folder:
                manager = self.manager(FakeAPI([data]), folder)
                result = manager.prepare(THREAD_ID, 'Exact title', 'message', 'operation-0001')
                self.assertEqual(result['errorCode'], code)

    def test_prepare_preserves_seconds_milliseconds_and_iso_completed_at(self):
        now = time.time()
        values = (now, now * 1000, datetime.fromtimestamp(now, timezone.utc).isoformat())
        for index, completed_at in enumerate(values):
            with self.subTest(completed_at=completed_at), tempfile.TemporaryDirectory() as folder:
                api = FakeAPI([snapshot(completed_at=completed_at)])
                manager = self.manager(api, folder)
                result = manager.prepare(THREAD_ID, 'Exact title', 'message', 'operation-0001')
                self.assertEqual(result['state'], 'awaiting_confirmation')
                self.assertEqual(result['baseline_completed_at'], completed_at)
                self.assertEqual(result['compactState']['completedAt'], completed_at)

    def test_session_and_operation_id_are_bound(self):
        with tempfile.TemporaryDirectory() as folder:
            manager = self.manager(FakeAPI([snapshot()]), folder)
            manager.prepare(THREAD_ID, 'Exact title', 'message', 'operation-0001')
            busy = manager.prepare(THREAD_ID, 'Exact title', 'other', 'operation-0002')
            conflict = manager.prepare('22222222-2222-4222-8222-222222222222', 'Other', 'x', 'operation-0001')
            self.assertEqual(busy['errorCode'], 'SESSION_JOB_EXISTS')
            self.assertEqual(conflict['errorCode'], 'OPERATION_ID_CONFLICT')

    def test_confirm_rejects_changed_baseline_without_mutating_native_session(self):
        with tempfile.TemporaryDirectory() as folder:
            api = FakeAPI([snapshot(), snapshot(turn_id='turn1')])
            manager = self.manager(api, folder)
            manager.prepare(THREAD_ID, 'Exact title', 'message', 'operation-0001')
            result = manager.confirm('operation-0001')
            self.assertEqual(result['state'], 'rejected')
            self.assertEqual(result['errorCode'], 'BASELINE_CHANGED')
            self.assertEqual(result['delivery_state'], 'not-sent')
            self.assertEqual([action for action, _ in api.calls], ['status', 'status'])

    def test_compact_waits_for_native_compaction_then_rechecks_before_send(self):
        with tempfile.TemporaryDirectory() as folder:
            clock = FakeClock()
            compact = snapshot(turn_id='turn1', compacted=True)
            api = FakeAPI([snapshot(), snapshot(), snapshot(), snapshot(thread_state='active'), compact, compact])
            manager = self.manager(api, folder, clock=lambda: clock.now, sleep=clock.sleep)
            manager.prepare(THREAD_ID, 'Exact title', 'original message', 'operation-0001')
            manager.confirm('operation-0001', 'compact')
            self.assertTrue(wait_for(lambda: manager.status('operation-0001')['state'] == 'delivery_accepted'))
            status = manager.status('operation-0001')
            self.assertFalse(status['worker_alive'])
            self.assertTrue(status['compact_complete'])
            self.assertEqual(status['compact_turn_id'], 'turn1')
            self.assertEqual(status['delivery_state'], 'accepted')
            self.assertEqual(clock.sleeps, [2.0, 2.0])
            start = next(args for action, args in api.calls if action == 'compact_start')
            send = next(args for action, args in api.calls if action == 'send')
            self.assertEqual(start['expected_baseline_turn_id'], 'turn0')
            self.assertEqual(send['expected_baseline_turn_id'], 'turn1')
            self.assertEqual(send['operation_id'], status['delivery_operation_id'])
            self.assertEqual(send['message'], 'original message')

    def test_unknown_compaction_start_never_polls_or_sends(self):
        with tempfile.TemporaryDirectory() as folder:
            api = FakeAPI([snapshot(), snapshot()], start={'deliveryState': 'unknown'})
            manager = self.manager(api, folder)
            manager.prepare(THREAD_ID, 'Exact title', 'message', 'operation-0001')
            manager.confirm('operation-0001')
            self.assertTrue(wait_for(lambda: not manager.status('operation-0001')['worker_alive']))
            status = manager.status('operation-0001')
            self.assertEqual(status['state'], 'compaction_unknown')
            self.assertFalse(status['compact_complete'])
            self.assertFalse(any(action == 'send' for action, _ in api.calls))

    def test_deadline_times_out_without_send_and_poll_interval_is_bounded(self):
        with tempfile.TemporaryDirectory() as folder:
            clock = FakeClock()
            api = FakeAPI([snapshot(), snapshot()])
            manager = self.manager(api, folder, clock=lambda: clock.now, sleep=clock.sleep, deadline_seconds=4)
            manager.prepare(THREAD_ID, 'Exact title', 'message', 'operation-0001')
            manager.confirm('operation-0001')
            self.assertTrue(wait_for(lambda: not manager.status('operation-0001')['worker_alive']))
            status = manager.status('operation-0001')
            self.assertEqual(status['state'], 'compaction_timeout')
            self.assertEqual(clock.sleeps, [2.0, 2.0])
            self.assertFalse(any(action == 'send' for action, _ in api.calls))

    def test_existing_compaction_skips_start_and_send_without_compact_sets_native_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            compacted = snapshot(compacted=True)
            api = FakeAPI([compacted, compacted, compacted])
            manager = self.manager(api, folder)
            manager.prepare(THREAD_ID, 'Exact title', 'message', 'operation-0001')
            manager.confirm('operation-0001', 'compact')
            self.assertTrue(wait_for(lambda: not manager.status('operation-0001')['worker_alive']))
            self.assertFalse(any(action == 'compact_start' for action, _ in api.calls))
            self.assertTrue(next(args for action, args in api.calls if action == 'send')['skip_compaction'])

        with tempfile.TemporaryDirectory() as folder:
            api = FakeAPI([snapshot(), snapshot(), snapshot()])
            manager = self.manager(api, folder)
            manager.prepare(THREAD_ID, 'Exact title', 'message', 'operation-0001')
            manager.confirm('operation-0001', 'send_without_compact')
            self.assertTrue(wait_for(lambda: not manager.status('operation-0001')['worker_alive']))
            self.assertFalse(any(action == 'compact_start' for action, _ in api.calls))
            self.assertTrue(next(args for action, args in api.calls if action == 'send')['skip_compaction'])

    def test_status_is_read_only_and_restart_marks_lost_payload_for_reconfirmation(self):
        with tempfile.TemporaryDirectory() as folder:
            api = FakeAPI([snapshot()])
            first = CompactJobs(api, root=folder)
            first.prepare(THREAD_ID, 'Exact title', 'message', 'operation-0001')
            before = Path(folder, 'compact-jobs.sqlite').read_bytes()
            first.status('operation-0001')
            self.assertEqual(Path(folder, 'compact-jobs.sqlite').read_bytes(), before)
            second = self.manager(FakeAPI(), folder)
            status = second.status('operation-0001')
            self.assertEqual(status['state'], 'interrupted')
            self.assertTrue(status['reconfirmation_required'])
            self.assertFalse(status['worker_alive'])
            self.assertEqual(api.calls, [('status', {'thread_id': THREAD_ID})])
            first.close()

    def test_close_during_confirmation_prevents_start_and_send(self):
        with tempfile.TemporaryDirectory() as folder:
            entered, release = threading.Event(), threading.Event()
            calls = []

            def api(action, arguments):
                calls.append(action)
                if len(calls) == 2:
                    entered.set()
                    release.wait(1)
                return snapshot()

            manager = CompactJobs(api, root=folder)
            manager.prepare(THREAD_ID, 'Exact title', 'message', 'operation-0001')
            result = []
            thread = threading.Thread(target=lambda: result.append(manager.confirm('operation-0001')))
            thread.start()
            self.assertTrue(entered.wait(1))
            manager.close(timeout=0.01)
            release.set()
            thread.join(1)
            self.assertEqual(result[0]['state'], 'interrupted')
            self.assertEqual(calls, ['status', 'status'])

    def test_job_capacity_is_64(self):
        with tempfile.TemporaryDirectory() as folder:
            def api(action, args):
                return snapshot(thread_id=args['thread_id'], title='Exact title')

            manager = self.manager(api, folder)
            for n in range(64):
                thread_id = f'{n:08x}-1111-4111-8111-111111111111'
                result = manager.prepare(thread_id, 'Exact title', 'message', f'operation-{n:04d}')
                self.assertEqual(result['state'], 'awaiting_confirmation')
            result = manager.prepare('ffffffff-1111-4111-8111-111111111111', 'Exact title', 'x', 'operation-9999')
            self.assertEqual(result['errorCode'], 'JOB_CAPACITY')

    def test_terminal_receipts_free_active_capacity_but_persisted_receipts_are_capped(self):
        with tempfile.TemporaryDirectory() as folder:
            def api(action, args):
                return snapshot(thread_id=args['thread_id'])

            manager = self.manager(api, folder)
            manager.MAX_RECEIPTS = 2
            first = manager.prepare(THREAD_ID, 'Exact title', 'message', 'operation-0001')
            self.assertEqual(first['state'], 'awaiting_confirmation')
            manager.confirm('operation-0001', 'cancel')
            second_id = '22222222-2222-4222-8222-222222222222'
            second = manager.prepare(second_id, 'Exact title', 'message', 'operation-0002')
            self.assertEqual(second['state'], 'awaiting_confirmation')
            manager.confirm('operation-0002', 'cancel')
            third = manager.prepare('33333333-3333-4333-8333-333333333333', 'Exact title',
                                    'message', 'operation-0003')
            self.assertEqual(third['errorCode'], 'RECEIPT_CAPACITY')


if __name__ == '__main__':
    unittest.main()
