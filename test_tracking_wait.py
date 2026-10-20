import threading
import time
import unittest
from unittest.mock import patch

from tracking import TrackingManager
from test_tracking import FakeClock, ManualWaiter, THREAD_ID, codex, wait_for


class TrackingWaitTests(unittest.TestCase):
    def manager(self, sampler=lambda: {'state': 'same'}, **options):
        waiter = ManualWaiter()
        manager = TrackingManager(clock=FakeClock(), waiter=waiter)
        self.addCleanup(manager.close)
        tracking_id = manager.start('computer', 'fake-client', sampler, **options)['tracking_id']
        return manager, waiter, tracking_id

    def baseline(self, manager, tracking_id):
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['events']))
        return manager.status(tracking_id)['latest_sequence']

    def query(self, manager, tracking_id, **options):
        resolved, done = threading.Event(), threading.Event()
        result = {}
        original = manager._get_locked

        def get_entry(value):
            entry = original(value)
            if threading.current_thread() is worker:
                resolved.set()
            return entry

        def run():
            try:
                result['status'] = manager.status(tracking_id, **options)
            except BaseException as exc:
                result['error'] = exc
            finally:
                done.set()

        worker = threading.Thread(target=run, daemon=True)
        with patch.object(manager, '_get_locked', side_effect=get_entry):
            worker.start()
            self.assertTrue(wait_for(lambda: resolved.is_set() or done.is_set()))
            if 'error' in result:
                raise result['error']
            # Acquiring this lock proves the query has entered Condition.wait or returned.
            with manager._lock:
                pass
        self.addCleanup(worker.join, 1)
        return done, result

    def response(self, done, result, reason):
        self.assertTrue(done.wait(1), 'long poll was not notified')
        if 'error' in result:
            raise result['error']
        status = result['status']
        self.assertEqual(status['wait']['reason'], reason)
        self.assertGreaterEqual(status['wait']['elapsed_seconds'], 0)
        return status

    def test_default_zero_is_immediate_and_preserves_status(self):
        manager, _, tracking_id = self.manager()
        cursor = self.baseline(manager, tracking_id)
        status = manager.status(tracking_id, after_sequence=cursor)
        self.assertEqual(status['wait']['reason'], 'immediate')
        self.assertEqual(status['wait']['requested_seconds'], 0)
        self.assertEqual(status['events'], [])
        self.assertEqual(status['state'], 'running')
        self.assertEqual(status['interval_seconds'], 30)

    def test_wait_seconds_rejects_nonfinite_bool_and_out_of_bounds(self):
        manager, _, tracking_id = self.manager()
        for value in (True, False, None, '1', [], -0.001, 25.001,
                      float('nan'), float('inf'), -float('inf'), 10 ** 1000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                manager.status(tracking_id, wait_seconds=value)

    def test_existing_events_return_immediately_and_keep_cursor_limit(self):
        manager, waiter, tracking_id = self.manager()
        self.baseline(manager, tracking_id)
        manager._trackers[tracking_id]['sampler'] = lambda: {'state': 'changed'}
        waiter.step()
        self.assertTrue(wait_for(lambda: manager.status(tracking_id)['latest_sequence'] == 2))
        for value in (0, 25, 25.0):
            status = manager.status(tracking_id, after_sequence=0, limit=1, wait_seconds=value)
            self.assertEqual(status['wait']['reason'], 'immediate')
            self.assertEqual(status['wait']['requested_seconds'], value)
            self.assertLess(status['wait']['elapsed_seconds'], 0.5)
            self.assertEqual([event['sequence'] for event in status['events']], [1])

    def test_timeout_uses_real_elapsed_time_even_with_frozen_sample_clock(self):
        manager, _, tracking_id = self.manager()
        cursor = self.baseline(manager, tracking_id)
        status = manager.status(tracking_id, after_sequence=cursor, wait_seconds=0.06)
        self.assertEqual(status['wait']['reason'], 'timeout')
        self.assertEqual(status['wait']['requested_seconds'], 0.06)
        self.assertGreaterEqual(status['wait']['elapsed_seconds'], 0.05)
        self.assertLess(status['wait']['elapsed_seconds'], 0.75)
        self.assertEqual(status['sample_count'], 1)
        self.assertEqual(status['events'], [])

    def test_unchanged_completed_sample_notifies_heartbeat(self):
        manager, waiter, tracking_id = self.manager()
        cursor = self.baseline(manager, tracking_id)
        done, result = self.query(manager, tracking_id, after_sequence=cursor, wait_seconds=0.8)
        self.assertFalse(done.is_set())
        waiter.step()
        status = self.response(done, result, 'heartbeat')
        self.assertEqual(status['events'], [])
        self.assertEqual(status['sample_count'], 2)
        self.assertEqual(status['state'], 'running')
        self.assertEqual(status['interval_seconds'], 30)

    def test_inflight_sample_and_spurious_notification_are_not_heartbeat(self):
        for query_during_sample in (False, True):
            with self.subTest(query_during_sample=query_during_sample):
                entered, release = threading.Event(), threading.Event()
                calls = []

                def sample():
                    calls.append(1)
                    if len(calls) == 2:
                        entered.set()
                        release.wait(2)
                    return {'state': 'same'}

                manager, waiter, tracking_id = self.manager(sample)
                self.addCleanup(release.set)
                cursor = self.baseline(manager, tracking_id)
                if query_during_sample:
                    waiter.step()
                    self.assertTrue(entered.wait(1))
                done, result = self.query(manager, tracking_id, after_sequence=cursor, wait_seconds=0.8)
                if not query_during_sample:
                    waiter.step()
                    self.assertTrue(entered.wait(1))
                with manager._lock:
                    manager._trackers[tracking_id]['condition'].notify_all()
                self.assertFalse(done.wait(0.04), 'inflight sample was treated as complete')
                release.set()
                status = self.response(done, result, 'heartbeat')
                self.assertEqual(status['sample_count'], 2)
                self.assertEqual(len(calls), 2)

    def test_first_completed_baseline_notifies_every_waiter(self):
        entered, release = threading.Event(), threading.Event()

        def sample():
            entered.set()
            release.wait(2)
            return {'state': 'baseline'}

        manager, _, tracking_id = self.manager(sample)
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(1))
        queries = [self.query(manager, tracking_id, wait_seconds=0.8) for _ in range(2)]
        for done, _ in queries:
            self.assertFalse(done.is_set())
        release.set()
        for done, result in queries:
            status = self.response(done, result, 'events')
            self.assertEqual(status['events'][0]['type'], 'baseline')
            self.assertIsNotNone(status['current_state'])

    def test_changed_sample_notifies_events(self):
        state = {'state': 'first'}
        manager, waiter, tracking_id = self.manager(lambda: state.copy())
        cursor = self.baseline(manager, tracking_id)
        done, result = self.query(manager, tracking_id, after_sequence=cursor, wait_seconds=0.8)
        state['state'] = 'second'
        waiter.step()
        status = self.response(done, result, 'events')
        self.assertEqual(status['events'][0]['type'], 'changed')

    def test_sampling_error_notifies_and_does_not_expose_old_state(self):
        state = {'state': 'first'}
        manager, waiter, tracking_id = self.manager(lambda: state.copy())
        cursor = self.baseline(manager, tracking_id)
        done, result = self.query(manager, tracking_id, after_sequence=cursor, wait_seconds=0.8)
        state.update(ok=False, errorCode='TEST_TIMEOUT', error='private detail')
        waiter.step()
        status = self.response(done, result, 'events')
        self.assertTrue(status['degraded'])
        self.assertEqual(status['error_count'], 1)
        self.assertIsNone(status['current_state'])
        self.assertNotIn('private detail', str(status))
        done, result = self.query(manager, tracking_id, after_sequence=status['latest_sequence'],
                                  wait_seconds=0.8)
        self.assertFalse(done.is_set(), 'a consumed error must wait for a new completed sample')
        waiter.step()
        status = self.response(done, result, 'heartbeat')
        self.assertTrue(status['degraded'])
        self.assertEqual(status['error_count'], 2)
        self.assertEqual(status['events'], [])

    def test_future_cursor_does_not_hide_completed_error(self):
        state = {'state': 'first'}
        manager, waiter, tracking_id = self.manager(lambda: state.copy())
        self.baseline(manager, tracking_id)
        done, result = self.query(manager, tracking_id, after_sequence=999, wait_seconds=0.8)
        state.update(ok=False, errorCode='TEST_TIMEOUT')
        waiter.step()
        status = self.response(done, result, 'heartbeat')
        self.assertTrue(status['degraded'])
        self.assertEqual(status['events'], [])

    def test_wait_releases_lock_for_queries_and_stop_then_terminal_is_immediate(self):
        manager, _, tracking_id = self.manager()
        cursor = self.baseline(manager, tracking_id)
        other_id = manager.start('computer', 'another-fake-client', lambda: {})['tracking_id']
        done, result = self.query(manager, tracking_id, after_sequence=cursor, wait_seconds=0.8)
        started = time.monotonic()
        self.assertEqual(manager.status(other_id)['state'], 'running')
        stopped = manager.stop(tracking_id, timeout=0.2)
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(stopped['terminal_reason'], 'stopped')
        self.response(done, result, 'terminal')
        status = manager.status(tracking_id, after_sequence=999, wait_seconds=25)
        self.assertEqual(status['wait']['reason'], 'terminal')
        self.assertLess(status['wait']['elapsed_seconds'], 0.5)

    def test_close_notifies_terminal(self):
        manager, _, tracking_id = self.manager()
        cursor = self.baseline(manager, tracking_id)
        done, result = self.query(manager, tracking_id, after_sequence=cursor, wait_seconds=0.8)
        manager.close(timeout=0.2)
        status = self.response(done, result, 'terminal')
        self.assertEqual(status['terminal_reason'], 'manager_closed')

    def test_deadline_notifies_terminal_without_new_sample(self):
        manager, waiter, tracking_id = self.manager(deadline_seconds=10)
        cursor = self.baseline(manager, tracking_id)
        done, result = self.query(manager, tracking_id, after_sequence=cursor, wait_seconds=0.8)
        manager._clock.value += 10
        waiter.step()
        status = self.response(done, result, 'terminal')
        self.assertEqual(status['terminal_reason'], 'deadline')
        self.assertEqual(status['sample_count'], 1)

    def test_task_completion_is_published_atomically_with_terminal(self):
        waiter = ManualWaiter()
        state = codex('active', 'turn', 'inProgress')
        manager = TrackingManager(clock=FakeClock(), waiter=waiter)
        self.addCleanup(manager.close)
        tracking_id = manager.start('codex', THREAD_ID, lambda: state.copy(),
                                    mode='task_complete')['tracking_id']
        cursor = self.baseline(manager, tracking_id)
        done, result = self.query(manager, tracking_id, after_sequence=cursor, wait_seconds=0.8)
        state.update(codex('idle', 'turn', 'completed'))
        waiter.step()
        status = self.response(done, result, 'terminal')
        self.assertEqual(status['terminal_reason'], 'task_complete')


if __name__ == '__main__':
    unittest.main()
