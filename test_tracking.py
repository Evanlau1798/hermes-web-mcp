import re
import threading
import time
import unittest

from tracking import TrackingManager


THREAD_ID = 'f9edab9f-ec7a-4e40-8764-d0712b4ee04d'


class ManualWaiter:
    def __init__(self):
        self._step = threading.Event()

    def __call__(self, stop_event, _timeout):
        while not stop_event.is_set():
            if self._step.wait(0.01):
                self._step.clear()
                return False
        return True

    def step(self):
        self._step.set()


class FakeClock:
    def __init__(self, value=100.0):
        self.value = value

    def __call__(self):
        return self.value


def wait_for(predicate, timeout=1.0):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        if predicate():
            return True
        threading.Event().wait(0.005)
    return predicate()


def advance(manager, waiter, tracking_id):
    before = manager.status(tracking_id)['sample_count']
    waiter.step()
    return wait_for(lambda: manager.status(tracking_id)['sample_count'] > before)


def codex(thread_status, turn_id=None, turn_status=None):
    latest = {'id': turn_id, 'status': turn_status} if turn_id else None
    return {'thread': {'id': THREAD_ID, 'status': {'type': thread_status}}, 'latestTurn': latest}


class TrackingTests(unittest.TestCase):
    def test_failed_query_does_not_present_prior_idle_as_current_state(self):
        manager, waiter = self.manager()
        samples = [codex('idle', 'old', 'completed'),
                   {'ok': False, 'errorCode': 'DESKTOP_TIMEOUT', 'stage': 'request'}]
        started = manager.start('codex', THREAD_ID, lambda: samples.pop(0))
        tracking_id = started['tracking_id']
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['current_state'] is not None))
        waiter.step()
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['degraded']))
        self.assertIsNone(manager.status(tracking_id)['current_state'])
        self.assertIsNone(manager.status(tracking_id)['terminal_reason'])

    def manager(self, clock=None):
        waiter = ManualWaiter()
        manager = TrackingManager(clock=clock or FakeClock(), waiter=waiter)
        self.addCleanup(manager.close)
        return manager, waiter

    def test_start_creates_running_tracker_and_same_process_status(self):
        manager, _ = self.manager()
        payload = {'summary': 'safe observed state', 'text': 'private accessible text'}
        started = manager.start('computer', 'client-session', lambda: payload)
        self.assertRegex(started['tracking_id'], re.compile(r'^[0-9a-f]{32}$'))
        self.assertEqual(started['state'], 'running')
        self.assertTrue(wait_for(lambda: manager.status(started['tracking_id'])['events']))
        status = manager.status(started['tracking_id'])
        self.assertEqual(status['state'], 'running')
        self.assertEqual(status['events'][0]['type'], 'baseline')
        self.assertEqual(status['current_state']['summary'], 'safe observed state')
        self.assertIn('heartbeat_monotonic_seconds', status)
        self.assertNotIn(payload['text'], str(status))

    def test_codex_needs_witnessed_active_turn_and_ignores_not_loaded_idle(self):
        manager, waiter = self.manager()
        samples = [codex('idle', 'old', 'completed'), codex('notLoaded'),
                   codex('idle', 'old', 'completed'), codex('active', 'new', 'inProgress'),
                   codex('active', 'new', 'completed'), codex('idle', 'new', 'completed')]
        started = manager.start('codex', THREAD_ID, lambda: samples.pop(0), mode='task_complete')
        tracking_id = started['tracking_id']
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['sample_count'] >= 1
                                 and manager.status(tracking_id)['last_success_monotonic_seconds'] is not None))
        for _ in range(3):
            self.assertTrue(advance(manager, waiter, tracking_id))
            self.assertIsNone(manager.status(tracking_id)['terminal_reason'])
        self.assertTrue(advance(manager, waiter, tracking_id))
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['current_state']['latest_turn']['status']
                                 == 'completed'))
        self.assertEqual(manager.status(tracking_id)['current_state']['thread_status'], 'active')
        self.assertIsNone(manager.status(tracking_id)['terminal_reason'])
        self.assertTrue(advance(manager, waiter, tracking_id))
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['terminal_reason'] == 'task_complete'))

    def test_codex_expected_turn_still_waits_for_idle(self):
        manager, waiter = self.manager()
        samples = [codex('active', 'turn-7', 'completed'), codex('idle', 'turn-7', 'completed')]
        started = manager.start('codex', THREAD_ID, lambda: samples.pop(0),
                                mode='task_complete', expected_turn_id='turn-7')
        tracking_id = started['tracking_id']
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['current_state'] is not None))
        self.assertEqual(manager.status(tracking_id)['current_state']['latest_turn']['id'], 'turn-7')
        self.assertEqual(manager.status(tracking_id)['current_state']['thread_status'], 'active')
        self.assertIsNone(manager.status(tracking_id)['terminal_reason'])
        self.assertTrue(advance(manager, waiter, tracking_id))
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['terminal_reason'] == 'task_complete'))

    def test_computer_completion_requires_literal_observable_predicate(self):
        manager, waiter = self.manager()
        state = {'ax': {'text': 'Still working'}}
        started = manager.start('computer', 'client', lambda: state, mode='task_complete',
                                completion_predicate={'path': ['ax', 'text'], 'contains': 'Task complete'})
        tracking_id = started['tracking_id']
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['last_success_monotonic_seconds'] is not None))
        self.assertIsNone(manager.status(tracking_id)['terminal_reason'])
        state['ax']['text'] = 'Task complete: private detail'
        waiter.step()
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['terminal_reason'] == 'task_complete'))
        self.assertNotIn('private detail', str(manager.status(tracking_id)))

    def test_sampling_errors_are_degraded_without_exposing_error_text(self):
        manager, waiter = self.manager()
        calls = [0]

        def sample():
            calls[0] += 1
            if calls[0] == 1:
                raise RuntimeError('password=never-store-this')
            return {'text': 'safe'}

        started = manager.start('computer', 'client', sample)
        tracking_id = started['tracking_id']
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['error_count'] == 1))
        self.assertTrue(manager.status(tracking_id)['degraded'])
        self.assertTrue(advance(manager, waiter, tracking_id))
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['last_success_monotonic_seconds'] is not None
                                 and not manager.status(tracking_id)['degraded']))
        status = manager.status(tracking_id)
        self.assertFalse(status['degraded'])
        self.assertNotIn('never-store-this', str(status))

    def test_structured_sampler_error_keeps_safe_code_and_stage(self):
        manager, _ = self.manager()
        started = manager.start('codex', THREAD_ID,
                                lambda: {'ok': False, 'errorCode': 'DESKTOP_PIPE_MISSING',
                                         'error': 'password=never-store-this'},
                                mode='task_complete')
        tracking_id = started['tracking_id']
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['error_count'] == 1))
        status = manager.status(tracking_id)
        self.assertEqual(status['last_error'], {'stage': 'codex_status',
                                                'errorCode': 'DESKTOP_PIPE_MISSING'})
        self.assertNotIn('never-store-this', str(status))

    def test_snapshot_and_incremental_event_queue_are_bounded(self):
        manager, waiter = self.manager()
        calls = [0]

        def sample():
            calls[0] += 1
            return {'text': 'x' * 300_000} if calls[0] == 1 else {'state': calls[0]}

        started = manager.start('computer', 'client', sample)
        tracking_id = started['tracking_id']
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['error_count'] == 1))
        for _ in range(70):
            self.assertTrue(advance(manager, waiter, tracking_id))
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['events_dropped'] > 0))
        status = manager.status(tracking_id)
        self.assertLessEqual(len(status['events']), manager.MAX_EVENTS)
        self.assertGreater(status['events_dropped'], 0)
        self.assertNotIn('x' * 100, str(status))

    def test_stop_discards_inflight_result_and_prevents_later_callbacks(self):
        manager, _ = self.manager()
        entered, release = threading.Event(), threading.Event()
        calls = []

        def sample():
            calls.append(1)
            entered.set()
            release.wait(1)
            return {'text': 'late result'}

        started = manager.start('computer', 'client', sample)
        tracking_id = started['tracking_id']
        self.assertTrue(entered.wait(1))
        stopped = manager.stop(tracking_id, timeout=0.01)
        self.assertIn(stopped['state'], ('stopping', 'terminal'))
        release.set()
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['terminal_reason'] == 'stopped'))
        self.assertEqual(len(calls), 1)
        self.assertNotIn('late result', str(manager.status(tracking_id)))

    def test_close_is_bounded_and_worker_exits_after_inflight_read_returns(self):
        manager, _ = self.manager()
        entered, release = threading.Event(), threading.Event()
        calls = []

        def sample():
            calls.append(1)
            entered.set()
            release.wait(1)
            return {'text': 'late result'}

        started = manager.start('computer', 'client', sample)
        tracking_id = started['tracking_id']
        self.assertTrue(entered.wait(1))
        result = manager.close(timeout=0.01)
        self.assertEqual(result['still_running'], 1)
        release.set()
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['terminal_reason'] == 'manager_closed'))
        self.assertEqual(len(calls), 1)
        self.assertNotIn('late result', str(manager.status(tracking_id)))

    def test_deadline_ends_tracking_with_reason(self):
        clock = FakeClock()
        manager, waiter = self.manager(clock)
        started = manager.start('computer', 'client', lambda: {'state': 'ok'}, deadline_seconds=10)
        tracking_id = started['tracking_id']
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['last_success_monotonic_seconds'] is not None))
        clock.value += 10
        waiter.step()
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['terminal_reason'] == 'deadline'))

    def test_computer_task_complete_rejects_missing_or_nonliteral_predicate(self):
        manager, _ = self.manager()
        with self.assertRaises(ValueError):
            manager.start('computer', 'client', lambda: {}, mode='task_complete')
        with self.assertRaises(ValueError):
            manager.start('computer', 'client', lambda: {}, mode='task_complete', completion_predicate=lambda _: True)

    def test_computer_callbacks_are_serialized_across_trackers(self):
        manager, _ = self.manager()
        entered = threading.Event()
        release = threading.Event()
        lock = threading.Lock()
        calls, active, maximum = [], [0], [0]

        def sample():
            with lock:
                calls.append(1)
                active[0] += 1
                maximum[0] = max(maximum[0], active[0])
            entered.set()
            release.wait(1)
            with lock:
                active[0] -= 1
            return {'state': len(calls)}

        manager.start('computer', 'one', sample)
        self.assertTrue(entered.wait(1))
        manager.start('computer', 'two', sample)
        threading.Event().wait(0.05)
        with lock:
            self.assertEqual(len(calls), 1)
        release.set()
        self.assertTrue(wait_for(lambda: len(calls) >= 2))
        self.assertEqual(maximum[0], 1)


if __name__ == '__main__':
    unittest.main()
