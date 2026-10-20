import threading
import unittest
from unittest.mock import Mock

import runtime
from backend import Bridge


class FeatureIntegrationTests(unittest.TestCase):
    def test_compact_dispatch_retains_original_payload_and_status_is_readonly(self):
        bridge = Bridge(codex=Mock())
        self.addCleanup(bridge.close)
        jobs = bridge.compact_jobs = Mock()
        jobs.prepare.return_value = {'state': 'awaiting_confirmation'}
        jobs.confirm.return_value = {'state': 'compacting'}
        jobs.status.return_value = {'state': 'compacting', 'worker_alive': True}
        args = {'thread_id': '11111111-1111-4111-8111-111111111111',
                'expected_title': 'Exact title', 'message': '原訊息 English', 'operation_id': 'operation-test'}
        bridge.call('codex_prepare_send', args)
        jobs.prepare.assert_called_once_with(**args)
        jobs.confirm.assert_called_once_with('operation-test', 'auto')
        jobs.confirm.reset_mock()
        bridge.call('codex_confirm_send', {'operation_id': 'operation-test', 'choice': 'compact', 'confirmed_by_user': True})
        jobs.confirm.assert_called_once_with(operation_id='operation-test', choice='compact')
        result = bridge.call('codex_operation_status', {'operation_id': 'operation-test'})
        self.assertTrue(result['worker_alive'])
        jobs.status.assert_called_once_with('operation-test')
        bridge.codex.assert_not_called()

    def test_compact_workflow_has_separate_readonly_status(self):
        bridge = Bridge(codex=Mock())
        self.addCleanup(bridge.close)
        self.assertFalse(bridge.specs['codex_prepare_send']['annotations']['read_only_hint'])
        self.assertFalse(bridge.specs['codex_confirm_send']['annotations']['read_only_hint'])
        self.assertTrue(bridge.specs['codex_operation_status']['annotations']['read_only_hint'])
        with self.assertRaises(ValueError):
            bridge.call('codex_confirm_send', {'operation_id': 'test-operation', 'choice': 'compact'})

    def test_tracking_has_real_worker_and_exact_readonly_computer_target(self):
        seen = threading.Event()
        def capture(arguments, **context):
            self.assertEqual(arguments, {'action': 'capture', 'mode': 'ax', 'pid': 42, 'window_id': 100})
            seen.set()
            return {'text': 'snapshot'}
        computer = Mock(computer_use=capture)
        bridge = Bridge(computer=computer)
        self.addCleanup(bridge.close)
        started = bridge.call('hermes_tracking', {'action': 'start', 'kind': 'computer',
                                                  'pid': 42, 'window_id': 100})
        self.assertTrue(seen.wait(2))
        status = bridge.call('hermes_tracking', {'action': 'status', 'tracking_id': started['tracking_id']})
        self.assertTrue(status['alive'])
        stopped = bridge.call('hermes_tracking', {'action': 'stop', 'tracking_id': started['tracking_id']})
        self.assertFalse(stopped['alive'])
        self.assertEqual(stopped['terminal_reason'], 'stopped')

    def test_tracking_rejects_foreground_and_partial_targets(self):
        bridge = Bridge()
        self.addCleanup(bridge.close)
        for args in ({'kind': 'computer'}, {'kind': 'computer', 'app': 'Discord'},
                     {'kind': 'computer', 'pid': 1}, {'kind': 'codex'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                bridge.call('hermes_tracking', {'action': 'start', **args})

    def test_formal_skill_update_is_write_tool(self):
        bridge = Bridge()
        self.addCleanup(bridge.close)
        self.assertFalse(bridge.specs['hermes_skill_manage']['annotations']['read_only_hint'])
        self.assertTrue(bridge.specs['hermes_tracking_status']['annotations']['read_only_hint'])


if __name__ == '__main__':
    unittest.main()
