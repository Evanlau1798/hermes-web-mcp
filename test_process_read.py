import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

import runtime
import mcp_types as types
from backend import Bridge, INSTRUCTIONS


class ProcessReadTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.bridge = Bridge(native=lambda name, args, **kw: self.calls.append((name, args, kw)) or '{}')

    def test_read_metadata_context_and_schema(self):
        spec = self.bridge.specs['hermes_process_read']
        wire = types.Tool(**spec).model_dump(by_alias=True)
        self.assertTrue(wire['annotations']['readOnlyHint'])
        self.assertFalse(wire['annotations']['destructiveHint'])
        self.assertFalse(wire['annotations']['openWorldHint'])
        self.assertEqual(spec['input_schema']['properties']['action']['enum'], ['list', 'poll', 'log'])
        self.assertFalse(self.bridge.specs['hermes_process_manage']['annotations']['read_only_hint'])
        context = self.bridge.call('hermes_context', {})
        self.assertTrue(context['tools']['hermes_process_read']['read_only'])
        self.assertEqual(context['processes']['read'], 'hermes_process_read')
        self.assertIn('hermes_process_read', INSTRUCTIONS)

    def test_bootstrap_owner_fence_and_dispatch(self):
        args = {'action': 'poll', 'session_id': 'owned'}
        self.assertEqual(self.bridge.call('hermes_process_read', args, client_scope='a')['errorCode'], 'bootstrap_required')
        self.bridge.call('hermes_context', {}, client_scope='a')
        for owner in ('foreign', self.bridge.owner):
            with patch('backend.process_registry.get', return_value=SimpleNamespace(owner_task_id=owner)):
                if owner == 'foreign':
                    with self.assertRaises(ValueError):
                        self.bridge.call('hermes_process_read', args, client_scope='a')
                    self.assertEqual(self.calls, [])
                else:
                    self.bridge.call('hermes_process_read', args, client_scope='a')
        self.assertEqual(self.calls[-1][0:2], ('process_manage', args))
        self.assertEqual(self.calls[-1][2]['task_id'], self.bridge.owner)
        self.bridge.call('hermes_process_read', {'action': 'list'}, client_scope='a')
        self.assertEqual(self.calls[-1][1], {'action': 'list'})
        self.assertEqual(self.calls[-1][2]['task_id'], self.bridge.owner)
        with patch('backend.process_registry.get', return_value=None), self.assertRaises(ValueError):
            self.bridge.call('hermes_process_read', args, client_scope='a')

    def test_read_cannot_dispatch_controls_or_extra_arguments(self):
        for action in ('kill', 'write', 'submit', 'close', 'handoff', 'wait'):
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.bridge.call('hermes_process_read', {'action': action, 'session_id': 'owned'})
        for args in ({'action': 'poll'}, {'action': 'log', 'session_id': 'owned', 'data': 'input'},
                     {'action': 'list', 'timeout': 180}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.bridge.call('hermes_process_read', args)
        self.assertEqual(self.calls, [])

    def test_legacy_wait_is_short_bounded_without_mutating_input(self):
        with patch('backend.process_registry.get', return_value=SimpleNamespace(owner_task_id=self.bridge.owner)):
            for supplied, expected in ((None, 30), (5, 5), (180, 30)):
                args = {'action': 'wait', 'session_id': 'owned'}
                if supplied is not None:
                    args['timeout'] = supplied
                original = args.copy()
                result = self.bridge.call('hermes_process_manage', args)
                self.assertEqual(self.calls[-1][1]['timeout'], expected)
                self.assertEqual(args, original)
                self.assertEqual(result['wait_window_seconds'], expected)

    def test_busy_bridge_returns_without_execution_and_recovers(self):
        entered, release = threading.Event(), threading.Event()
        def native(name, args, **kw):
            entered.set()
            self.assertTrue(release.wait(6))
            return '{}'
        bridge = Bridge(native=native)
        with ThreadPoolExecutor(max_workers=2) as pool:
            active = pool.submit(bridge.call, 'hermes_read_file', {'path': 'unused'})
            try:
                self.assertTrue(entered.wait(2))
                pending = pool.submit(bridge.call, 'hermes_process_read', {'action': 'list'})
                result = pending.result(timeout=3)
                self.assertEqual(result['errorCode'], 'bridge_busy')
                self.assertFalse(result['executionStarted'])
                self.assertFalse(result['automaticRetrySafe'])
            finally:
                release.set()
            active.result(timeout=2)
        bridge.call('hermes_process_read', {'action': 'list'})


if __name__ == '__main__':
    unittest.main()
