import json
import subprocess
import tempfile
import unittest
import threading
import hashlib
import sys
import os
from pathlib import Path
from unittest.mock import Mock, patch

from codex_adapter import CodexAdapter


class CodexAdapterTests(unittest.TestCase):
    def test_missing_configured_node_never_dispatches(self):
        runner = Mock()
        with patch.dict(os.environ, {'HERMES_MCP_NODE': str(Path(__file__).parent / 'tmp' / 'missing-node.exe')}):
            result = CodexAdapter(runner=runner)('list', {})
        self.assertEqual(result['errorCode'], 'NODE_NOT_FOUND')
        self.assertEqual(result['deliveryState'], 'not-sent')
        runner.assert_not_called()

    def test_node_uses_configured_nondefault_installation(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / 'node.exe'
            executable.touch()
            runner = Mock(return_value=Mock(stdout='{"ok":true}'))
            with patch.dict(os.environ, {'HERMES_MCP_NODE': str(executable)}):
                self.assertTrue(CodexAdapter(runner=runner)('list', {})['ok'])
            self.assertEqual(runner.call_args.args[0][0], str(executable))

    def test_compact_timeout_is_unknown_and_never_retry_safe(self):
        result = CodexAdapter(runner=Mock(side_effect=subprocess.TimeoutExpired('node', 50)))('compact_start', {})
        self.assertEqual(result['deliveryState'], 'unknown')
        self.assertFalse(result['automaticRetrySafe'])

    def test_formal_cli_rejects_legacy_unsafe_input_before_dispatch(self):
        result = subprocess.run([sys.executable, str(Path(__file__).parent / 'codex_adapter.py')],
                                input=json.dumps({'action': 'send', 'thread_id': 'invalid', 'message': 'test'}),
                                capture_output=True, text=True, encoding='utf-8', timeout=15,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        output = json.loads(result.stdout)
        self.assertEqual(output['errorCode'], 'INVALID_CLI_INPUT')
        self.assertEqual(output['deliveryState'], 'not-sent')
        self.assertEqual(result.returncode, 1)

    def test_unknown_delivery_is_durable_and_never_resent(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent / 'tmp') as directory:
            runner = Mock(side_effect=subprocess.TimeoutExpired('node', 50))
            args = {'thread_id': 'test', 'message': 'PRIVATE', 'operation_id': 'test-operation-1'}
            first = CodexAdapter(Path(directory), runner=runner)('send', args)
            second = CodexAdapter(Path(directory), runner=runner)('send', args)
            self.assertEqual(first['deliveryState'], 'unknown')
            self.assertTrue(second['deduplicated'])
            self.assertEqual(second['requestId'], first['requestId'])
            self.assertEqual(runner.call_count, 1)
            conflict = CodexAdapter(Path(directory), runner=runner)('send', {**args, 'message': 'different'})
            self.assertTrue(conflict['deduplicated'])
            self.assertFalse(conflict['messageContentVerified'])
            target_conflict = CodexAdapter(Path(directory), runner=runner)('send', {**args, 'thread_id': 'other'})
            self.assertEqual(target_conflict['errorCode'], 'OPERATION_ID_CONFLICT')
            self.assertEqual(runner.call_count, 1)
            raw = (Path(directory) / 'codex-delivery.sqlite').read_bytes()
            self.assertNotIn(b'PRIVATE', raw)
            old_fingerprint = hashlib.sha256(json.dumps(['send', args], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            self.assertNotIn(old_fingerprint.encode(), raw)

    def test_query_os_and_protocol_failures_are_distinct_and_safe(self):
        for error, code, stage in [(FileNotFoundError(), 'NODE_NOT_FOUND', 'launch'),
                                   (PermissionError(), 'PERMISSION_DENIED', 'launch'),
                                   (subprocess.TimeoutExpired('node', 50), 'PROCESS_TIMEOUT', 'request')]:
            result = CodexAdapter(runner=Mock(side_effect=error))('status', {'thread_id': 'test'})
            self.assertEqual(result['errorCode'], code)
            self.assertEqual(result['stage'], stage)
            self.assertTrue(result['automaticRetrySafe'])
            self.assertIn('requestId', result)
        result = CodexAdapter(runner=Mock(return_value=Mock(stdout='bad', returncode=0)))('list', {})
        self.assertEqual(result['stage'], 'parse')
        self.assertEqual(result['errorCode'], 'INVALID_BRIDGE_OUTPUT')

    def test_structured_node_error_reaches_web_unchanged(self):
        payload = {'ok': False, 'errorCode': 'THREAD_NOT_FOUND', 'stage': 'lookup',
                   'requestState': 'rejected', 'requestId': 'safe-request-id'}
        runner = Mock(return_value=Mock(stdout=json.dumps(payload), returncode=1))
        self.assertEqual(CodexAdapter(runner=runner)('read', {}), payload)

    def test_bridge_query_preserves_adapter_diagnostics(self):
        from backend import Bridge
        diagnostic = {'ok': False, 'errorCode': 'THREAD_NOT_LOADED', 'stage': 'read',
                      'requestId': 'test-request', 'automaticRetrySafe': True,
                      'error': 'Desktop 回報 notLoaded，不能視為閒置。'}
        bridge = Bridge(codex=lambda *_: diagnostic)
        self.addCleanup(bridge.close)
        result = bridge.call('codex_status', {'thread_id': '11111111-1111-4111-8111-111111111111'})
        self.assertEqual(result, diagnostic)

    def test_busy_query_has_concrete_local_stage_and_no_execution(self):
        from backend import Bridge
        bridge = Bridge(codex=Mock())
        self.addCleanup(bridge.close)
        acquired, release = threading.Event(), threading.Event()
        def holder():
            with bridge.lock:
                acquired.set()
                release.wait(3)
        worker = threading.Thread(target=holder)
        worker.start()
        self.assertTrue(acquired.wait(1))
        try:
            result = bridge.call('codex_list', {})
            self.assertEqual(result['stage'], 'queue')
            self.assertEqual(result['requestState'], 'not-sent')
            self.assertEqual(result['source'], 'hermes-web-mcp')
            self.assertTrue(result['automaticRetrySafe'])
            bridge.codex.assert_not_called()
        finally:
            release.set()
            worker.join(2)


if __name__ == '__main__':
    unittest.main()
