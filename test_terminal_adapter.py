import os
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import runtime
from terminal_adapter import prepare_terminal


PWSH7 = (runtime.ROOT / 'tmp' / 'fixture pwsh.exe').as_posix()


class AdapterTests(unittest.TestCase):
    def test_powershell_uses_nondefault_installation_from_path(self):
        with tempfile.TemporaryDirectory() as folder:
            executable = Path(folder) / 'pwsh.exe'
            executable.touch()
            with patch.dict(os.environ, {'PATH': folder}), \
                    patch('terminal_adapter._guard_original'):
                _, metadata = prepare_terminal({'command': 'exit 0'})
            self.assertEqual(metadata['executable'], executable.as_posix())

    def test_default_prefers_path_pwsh_and_keeps_script_literal(self):
        script = 'Write-Output "繁體 $env:USERNAME"; Write-Output \'$(echo literal) ` "\''
        with patch('local_paths.shutil.which', return_value=PWSH7):
            native, metadata = prepare_terminal({'command': script})
        argv = shlex.split(native['command'])
        self.assertEqual(metadata, {'shell': 'powershell', 'executable': PWSH7})
        self.assertEqual(argv[:5], [PWSH7, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command'])
        self.assertEqual(len(argv), 6)
        self.assertIn('\n' + script + '\n', argv[5])
        self.assertNotIn('-EncodedCommand', native['command'])

    def test_bash_and_legacy_default_are_exact_pass_through(self):
        for options in ({'shell': 'bash'}, {}):
            arguments = {'command': 'printf "%s" "$HOME"', 'background': True,
                         'timeout': 9, 'workdir': 'G:/中文 folder', 'pty': True,
                         'notify': ['ready'],
                         'heartbeat': 30, 'persist_on_release': True, **options}
            original = dict(arguments)
            native, metadata = prepare_terminal(arguments, default_shell='bash')
            self.assertEqual(native, {k: v for k, v in arguments.items() if k != 'shell'})
            self.assertEqual(metadata, {'shell': 'bash', 'executable': None})
            self.assertEqual(arguments, original)
            self.assertIsNot(native, arguments)

    def test_explicit_shell_overrides_session_default(self):
        with patch.object(Path, 'is_file', return_value=True):
            native, metadata = prepare_terminal({'command': 'exit 0', 'shell': 'powershell',
                                                'background': True, 'timeout': 8}, default_shell='bash')
        self.assertEqual(metadata['shell'], 'powershell')
        self.assertNotIn('shell', native)
        self.assertTrue(native['background'])
        self.assertEqual(native['timeout'], 8)

    def test_fallback_to_windows_powershell_5(self):
        with patch('local_paths.shutil.which', return_value=None), patch.object(Path, 'is_file', return_value=True):
            native, metadata = prepare_terminal({'command': 'exit 0'})
        self.assertTrue(metadata['executable'].endswith('/System32/WindowsPowerShell/v1.0/powershell.exe'))
        self.assertEqual(shlex.split(native['command'])[0], metadata['executable'])

    def test_missing_powershell_fails_without_execution(self):
        with patch('local_paths.shutil.which', return_value=None), patch.object(Path, 'is_file', return_value=False):
            with self.assertRaises(FileNotFoundError):
                prepare_terminal({'command': 'exit 0'})

    def test_invalid_input_fails_closed(self):
        for arguments in (None, [], {}, {'command': ''}, {'command': 1},
                          {'command': 'a\0b'}, {'command': 'ok', 'shell': 'cmd'},
                          {'command': 'ok', 'shell': None}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                prepare_terminal(arguments)
        with self.assertRaises(ValueError):
            prepare_terminal({'command': 'ok'}, default_shell='cmd')

    def test_native_hardline_and_deny_still_see_script(self):
        from tools import approval_context
        from tools import terminal_tool
        from tools.approval_detection import detect_dangerous_command
        with patch.object(Path, 'is_file', return_value=True), \
                patch.object(terminal_tool, '_pre_exec_block'), \
                patch.object(terminal_tool, '_run_foreground') as execute:
            with self.assertRaisesRegex(ValueError, 'hardline'):
                prepare_terminal({'command': 'rm -rf /'})
            with patch.object(approval_context, '_get_approval_config',
                              return_value={'mode': 'off', 'deny': ['*forbidden-adapter-token*']}):
                with self.assertRaisesRegex(ValueError, 'deny'):
                    prepare_terminal({'command': 'Write-Output forbidden-adapter-token'})
            with patch.object(terminal_tool, '_check_all_guards',
                              return_value={'approved': False, 'message': 'guard unavailable'}):
                with self.assertRaisesRegex(ValueError, 'guard unavailable'):
                    prepare_terminal({'command': 'Write-Output ok'})
            execute.assert_not_called()
        self.assertTrue(detect_dangerous_command('Remove-Item C:/example -Recurse -Force')[0])

    def test_native_preexec_sees_original_and_rejection_stops_preparation(self):
        from tools import terminal_tool
        with patch.object(Path, 'is_file', return_value=True), \
                patch.object(terminal_tool, '_pre_exec_block',
                             side_effect=terminal_tool._Rejected('{"error":"native pre-exec block"}')) as preexec:
            with self.assertRaisesRegex(ValueError, 'native pre-exec block'):
                prepare_terminal({'command': 'Write-Output safe', 'workdir': str(runtime.ROOT)},
                                 session_key='adapter-owner')
        self.assertEqual(preexec.call_args.args, ('Write-Output safe',))
        self.assertEqual(preexec.call_args.kwargs['workdir'], str(runtime.ROOT))
        self.assertEqual(preexec.call_args.kwargs['session_key'], 'adapter-owner')

    def test_guard_timeout_and_nonlocal_backend_fail_closed(self):
        from tools import terminal_tool
        with patch.object(Path, 'is_file', return_value=True), \
                patch('agent.deadline.run_bounded_sync', return_value=SimpleNamespace(timed_out=True)), \
                patch.object(terminal_tool, '_check_all_guards') as approval:
            with self.assertRaisesRegex(ValueError, 'timed out'):
                prepare_terminal({'command': 'Write-Output safe'})
            approval.assert_not_called()
        with patch.object(Path, 'is_file', return_value=True), \
                patch.object(terminal_tool, '_get_env_config', return_value={'env_type': 'ssh'}):
            with self.assertRaisesRegex(ValueError, 'local backend'):
                prepare_terminal({'command': 'Write-Output safe'})

    def test_prepared_arguments_use_native_planner_and_background_handler(self):
        from tools import terminal_tool
        foreground, _ = prepare_terminal({'command': 'Write-Output "safe"'}, session_key='adapter-owner')
        plan = terminal_tool._plan_execution(foreground['command'], task_id='adapter-owner',
                                             timeout=5, background=False, _host_local=False)
        self.assertEqual(plan.env_type, 'local')
        arguments = {'command': 'Write-Output "ready"', 'background': True, 'timeout': 5,
                     'notify': ['ready'], 'heartbeat': 60, 'persist_on_release': True, 'pty': True}
        native, _ = prepare_terminal(arguments, session_key='adapter-owner')
        with patch.object(terminal_tool, 'terminal_tool', return_value='native-session') as execute:
            result = terminal_tool._handle_terminal(native, task_id='adapter-owner', session_id='adapter-owner')
        self.assertEqual(result, 'native-session')
        forwarded = execute.call_args.kwargs
        for key in ('command', 'background', 'timeout', 'heartbeat', 'persist_on_release', 'pty'):
            self.assertEqual(forwarded[key], native[key])
        self.assertEqual(forwarded['watch_patterns'], ['ready'])
        self.assertEqual(forwarded['task_id'], 'adapter-owner')
        self.assertEqual(forwarded['session_id'], 'adapter-owner')

    @unittest.skipUnless(os.name == 'nt', 'Windows shell boundary')
    def test_real_bash_to_powershell_utf8_quoting_and_exit_codes(self):
        import runtime
        from tools.environments.local import _find_bash
        # Every executed command is harmless; destructive cases only reach guard tests.
        cases = [
            ('Write-Output \'繁體 $(echo literal) ` " $HOME\' # trailing comment', 0,
             '繁體 $(echo literal) ` " $HOME'),
            ('Write-Error "adapter test"; Write-Output "continued"', 1, 'continued'),
            ('exit 23', 23, ''),
            ('Write-Error "adapter test"; exit 0', 0, ''),
            ('throw "adapter throw"', 1, 'adapter throw'),
            ('cmd.exe /d /c exit 19', 19, ''),
        ]
        for script, code, output in cases:
            with self.subTest(script=script):
                native, _ = prepare_terminal({'command': script})
                result = subprocess.run([_find_bash(), '-c', native['command']],
                                        capture_output=True, encoding='utf-8', timeout=20,
                                        creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(result.returncode, code, result.stderr)
                self.assertIn(output, result.stdout + result.stderr)

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell 5 fallback')
    def test_real_powershell_5_fallback(self):
        from tools.environments.local import _find_bash
        with patch('local_paths.shutil.which', return_value=None):
            native, metadata = prepare_terminal({'command': 'Write-Output "中文 fallback"; Write-Error "test"'})
        self.assertTrue(metadata['executable'].endswith('/powershell.exe'))
        result = subprocess.run([_find_bash(), '-c', native['command']], capture_output=True,
                                encoding='utf-8', timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn('中文 fallback', result.stdout)


if __name__ == '__main__':
    unittest.main()
