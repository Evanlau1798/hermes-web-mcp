import sqlite3
import tempfile
import threading
import unittest

from test_compact_jobs import CompactJobs, FakeAPI, FakeClock, THREAD_ID, snapshot, wait_for


class CompactRegressionTests(unittest.TestCase):
    def manager(self, api, folder, **kwargs):
        manager = CompactJobs(api, root=folder, **kwargs)
        self.addCleanup(lambda: manager.close() if manager.path.exists() else None)
        return manager

    def test_delivery_has_independent_id_and_unverified_duplicate_is_not_success(self):
        with tempfile.TemporaryDirectory() as folder:
            api = FakeAPI(send={'deliveryState': 'accepted', 'deduplicated': True,
                                'messageContentVerified': False})
            manager = self.manager(api, folder)
            prepared = manager.prepare(THREAD_ID, 'Exact title', 'original', 'operation-0001')
            self.assertNotEqual(prepared['delivery_operation_id'], 'operation-0001-delivery')
            manager.confirm('operation-0001', 'send_without_compact')
            self.assertTrue(wait_for(lambda: not manager.status('operation-0001')['worker_alive']))
            result = manager.status('operation-0001')
            self.assertEqual(result['delivery_state'], 'unknown')
            self.assertEqual(result['errorCode'], 'DELIVERY_ID_REUSED')
            send = next(args for action, args in api.calls if action == 'send')
            self.assertEqual(send['operation_id'], prepared['delivery_operation_id'])

    def test_completed_compaction_with_intervening_turn_never_sends(self):
        with tempfile.TemporaryDirectory() as folder:
            compact = snapshot(turn_id='compact1', compacted=True)
            compact['recentTurnIds'] = ['compact1', 'intervening-turn']
            api = FakeAPI([snapshot(), snapshot(), snapshot(), compact, compact])
            clock = FakeClock()
            manager = self.manager(api, folder, clock=lambda: clock.now, sleep=clock.sleep)
            manager.prepare(THREAD_ID, 'Exact title', 'original', 'operation-0001')
            manager.confirm('operation-0001')
            self.assertTrue(wait_for(lambda: not manager.status('operation-0001')['worker_alive']))
            self.assertFalse(any(action == 'send' for action, _ in api.calls))
            self.assertEqual(manager.status('operation-0001')['errorCode'], 'COMPACTION_NOT_CONFIRMED')

    def test_receipt_store_failure_after_dispatch_retains_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            api = FakeAPI()
            manager = self.manager(api, folder)
            manager.prepare(THREAD_ID, 'Exact title', 'original', 'operation-0001')
            update = manager._update
            def fail_final(operation_id, **values):
                if values.get('state') == 'delivery_accepted':
                    raise sqlite3.OperationalError('synthetic transient write failure')
                return update(operation_id, **values)
            manager._update = fail_final
            manager.confirm('operation-0001', 'send_without_compact')
            self.assertTrue(wait_for(lambda: not manager.status('operation-0001')['worker_alive']))
            self.assertEqual(sum(action == 'send' for action, _ in api.calls), 1)
            self.assertEqual(manager.status('operation-0001')['delivery_state'], 'unknown')

    def test_close_while_worker_status_is_blocked_prevents_compact_dispatch(self):
        with tempfile.TemporaryDirectory() as folder:
            entered, release = threading.Event(), threading.Event()
            calls = []
            def api(action, args):
                calls.append(action)
                if len(calls) == 3:
                    entered.set()
                    release.wait(2)
                return snapshot()
            manager = self.manager(api, folder)
            manager.prepare(THREAD_ID, 'Exact title', 'original', 'operation-0001')
            manager.confirm('operation-0001')
            self.assertTrue(entered.wait(1))
            manager.close(timeout=0.01)
            release.set()
            self.assertTrue(wait_for(lambda: not manager.status('operation-0001')['worker_alive']))
            self.assertNotIn('compact_start', calls)
            self.assertNotIn('send', calls)


if __name__ == '__main__':
    unittest.main()
