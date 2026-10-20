import tempfile
import os
from types import SimpleNamespace
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import runtime
from backend import Bridge


class BrowserTests(unittest.TestCase):
    def test_cleanup_failure_preserves_result_and_retries_on_poll(self):
        from browser_adapter import BrowserAdapter
        with tempfile.TemporaryDirectory() as root:
            adapter = BrowserAdapter(root=Path(root))
            folder = Path(root) / 'round'
            folder.mkdir()
            adapter.runs['owned'] = folder
            with patch('browser_adapter.shutil.rmtree', side_effect=PermissionError('locked')):
                result = adapter.collect('owned', {'status':'exited', 'exit_code':0})
            self.assertEqual(result.payload['exit_code'], 0)
            self.assertIn('cleanup_error', result.payload)
            self.assertIn('owned', adapter.runs)
            adapter.collect('owned', {'status':'exited', 'exit_code':0})
            self.assertNotIn('owned', adapter.runs)

    def test_shutdown_cleans_confirmed_exits_and_preserves_unknown(self):
        from browser_adapter import BrowserAdapter
        with tempfile.TemporaryDirectory() as root:
            adapter = BrowserAdapter(root=Path(root))
            for sid in ('done', 'unknown'):
                folder = Path(root) / sid
                folder.mkdir()
                adapter.runs[sid] = folder
            registry = Mock()
            registry.get.side_effect = lambda sid: SimpleNamespace(exited=sid=='done', completion_reason='exited')
            adapter.close(registry)
            self.assertFalse((Path(root)/'done').exists())
            self.assertTrue((Path(root)/'unknown').exists())

    def test_executable_precedence_and_invalid_override(self):
        from browser_adapter import ego_executable
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            binary = root / 'ego-browser.exe'
            binary.touch()
            with patch.dict(os.environ, {'HERMES_MCP_EGO_BROWSER': str(binary)}), \
                 patch('browser_adapter.shutil.which') as which:
                self.assertEqual(ego_executable(), binary)
                which.assert_not_called()
            with patch.dict(os.environ, {'HERMES_MCP_EGO_BROWSER': 'missing.exe'}):
                with self.assertRaises(ValueError):
                    ego_executable()
            with patch.dict(os.environ):
                os.environ.pop('HERMES_MCP_EGO_BROWSER', None)
                with patch('browser_adapter.shutil.which', return_value=str(binary)):
                    self.assertEqual(ego_executable(), binary)
                fallback = root / '.local/share/ego/active_version_dir/ego-browser.exe'
                fallback.parent.mkdir(parents=True)
                fallback.touch()
                with patch('browser_adapter.shutil.which', return_value=None), patch.object(Path, 'home', return_value=root):
                    self.assertEqual(ego_executable(), fallback)

    def test_single_tool_contract_and_validation(self):
        bridge = Bridge()
        self.addCleanup(bridge.close)
        self.assertIn('browser_use', bridge.specs)
        self.assertEqual(len(bridge.specs), 27)
        self.assertEqual(bridge.specs['browser_use']['title'], 'browser use')
        self.assertFalse(bridge.specs['browser_use']['annotations']['read_only_hint'])
        for args in ({'action': 'run'}, {'action': 'guide', 'code': 'x'},
                     {'action': 'run', 'code': ' '}, {'action': 'other'}):
            with self.subTest(args=args), self.assertRaises(ValueError):
                bridge.call('browser_use', args)

    def test_utf8_dispatch_guards_and_owned_background(self):
        from browser_adapter import BrowserAdapter
        with tempfile.TemporaryDirectory() as root:
            native = Mock(side_effect=[{'session_id': 'owned'},
                                       {'status': 'timeout', 'output': '繁體中文'}])
            adapter = BrowserAdapter(root=Path(root))
            with patch('browser_adapter.ego_executable', return_value=Path(root) / 'ego-browser.exe'), \
                 patch('browser_adapter._guard_original') as guard, \
                 patch('browser_adapter.prepare_terminal', side_effect=lambda a, **kw: (a, {})):
                result = adapter.call({'action': 'run', 'code': "console.log('繁體中文')"}, native, 'owner', None)
            self.assertEqual(result.payload['session_id'], 'owned')
            self.assertFalse(result.payload['automaticRetrySafe'])
            guard.assert_called_once()
            self.assertIn('繁體中文', guard.call_args.args[0])
            script = next(Path(root).glob('*/round.mjs'))
            self.assertIn('繁體中文', script.read_text(encoding='utf-8'))
            self.assertTrue(native.call_args_list[0].args[1]['background'])
            self.assertEqual(native.call_args_list[0].kwargs['task_id'], 'owner')
            self.assertEqual(native.call_args_list[1].args[1]['session_id'], 'owned')
            bounded = adapter.collect('owned', {'status': 'running', 'output': 'x' * 100000})
            self.assertEqual(bounded.payload['session_id'], 'owned')
            self.assertTrue(bounded.payload['truncated'])

    def test_dispatch_retains_session_after_wait_failure(self):
        from browser_adapter import BrowserAdapter
        with tempfile.TemporaryDirectory() as root:
            adapter = BrowserAdapter(root=Path(root))
            native = Mock(side_effect=[{'session_id':'owned'}, RuntimeError('connection lost')])
            with patch('browser_adapter.ego_executable', return_value=Path(root) / 'ego-browser.exe'), \
                 patch('browser_adapter._guard_original'), \
                 patch('browser_adapter.prepare_terminal', side_effect=lambda a, **kw: (a, {})):
                result = adapter.call({'action':'run', 'code':'console.log(1)'}, native, 'owner', None)
            self.assertEqual(result.payload['outcome'], 'unknown')
            self.assertEqual(result.payload['session_id'], 'owned')
            self.assertIn('owned', adapter.runs)

    def test_rejection_does_not_launch(self):
        from browser_adapter import BrowserAdapter
        with tempfile.TemporaryDirectory() as root:
            native = Mock()
            adapter = BrowserAdapter(root=Path(root))
            with patch('browser_adapter.ego_executable', return_value=Path(root) / 'ego-browser.exe'), \
                 patch('browser_adapter._guard_original', side_effect=ValueError('deny')):
                result = adapter.call({'action': 'run', 'code': 'blocked'}, native, 'owner', None)
            self.assertFalse(result.payload['executionStarted'])
            native.assert_not_called()
            self.assertEqual(list(Path(root).iterdir()), [])

    def test_images_are_owned_bounded_and_returned_after_poll(self):
        from browser_adapter import BrowserAdapter
        with tempfile.TemporaryDirectory() as root:
            adapter = BrowserAdapter(root=Path(root))
            folder = Path(root) / 'round'
            folder.mkdir()
            (folder / 'screenshot.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            adapter.runs['owned'] = folder
            active = adapter.collect('owned', {'status': 'running'})
            self.assertEqual(active.images, [])
            done = adapter.collect('owned', {'status': 'exited', 'exit_code': 0})
            self.assertEqual(done.images[0][0], 'image/png')
            self.assertFalse(folder.exists())
            self.assertIsNone(adapter.collect('foreign', {}))
            folder.mkdir()
            outside = Path(root) / 'private.png'
            outside.write_bytes(b'\x89PNG\r\n\x1a\nsecret')
            (folder / 'screenshot.png').write_bytes(outside.read_bytes())
            adapter.runs['owned'] = folder
            with patch.object(Path, 'is_symlink', side_effect=lambda: True):
                denied = adapter.collect('owned', {'status': 'exited', 'exit_code': 0})
            self.assertEqual(denied.images, [])
            self.assertIn('image_error', denied.payload)
            self.assertTrue(outside.exists())


if __name__ == '__main__':
    unittest.main()
