import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from local_paths import executable, powershell


class ExecutableTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def test_override_keeps_spaces_and_quotes_as_path_data(self):
        with tempfile.TemporaryDirectory(prefix="hermes 'quoted' ") as folder:
            path = Path(folder) / 'node.exe'
            path.touch()
            with patch.dict(os.environ, {'HERMES_MCP_NODE': str(path)}), \
                    patch('local_paths.shutil.which') as search:
                self.assertEqual(executable('node'), path)
            search.assert_not_called()

    def test_invalid_override_does_not_fall_back(self):
        with tempfile.TemporaryDirectory() as folder:
            for value in ('', 'pwsh.exe', str(Path(folder) / 'missing.exe'), folder):
                with self.subTest(value=value), patch.dict(os.environ, {'HERMES_MCP_PWSH': value}), \
                        patch('local_paths.shutil.which') as search:
                    with self.assertRaises(FileNotFoundError):
                        powershell()
                    search.assert_not_called()

    def test_path_resolution_and_missing_runtime(self):
        with patch('local_paths.shutil.which', return_value=str(Path.cwd() / 'node.exe')):
            self.assertEqual(executable('node'), Path.cwd() / 'node.exe')
        with patch('local_paths.shutil.which', return_value=None):
            with self.assertRaises(FileNotFoundError):
                executable('node')
            with self.assertRaises(FileNotFoundError):
                powershell()

    def test_windows_powershell_fallback_uses_system_root(self):
        with tempfile.TemporaryDirectory() as folder:
            fallback = Path(folder) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
            fallback.parent.mkdir(parents=True)
            fallback.touch()
            with patch.dict(os.environ, {'SystemRoot': folder}), \
                    patch('local_paths.shutil.which', return_value=None):
                self.assertEqual(powershell(), fallback)

    @unittest.skipUnless(os.name == 'nt', 'Windows native executable selection')
    def test_shell_wrappers_cannot_be_configured_as_executables(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'node.cmd'
            path.touch()
            with patch.dict(os.environ, {'HERMES_MCP_NODE': str(path)}):
                with self.assertRaises(FileNotFoundError):
                    executable('node')


if __name__ == '__main__':
    unittest.main()
