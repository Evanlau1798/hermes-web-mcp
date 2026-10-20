"""Windows lifecycle acceptance using a disposable executable, never the live Tunnel."""
import ctypes
import base64
import json
from ctypes import wintypes
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from local_paths import executable

ROOT = Path(__file__).resolve().parent
PWSH = executable('pwsh') if os.name == 'nt' else None
CSC = Path(os.environ.get('SystemRoot', '')) / 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'


@unittest.skipUnless(os.name == 'nt', 'Windows process jobs required')
class LauncherTests(unittest.TestCase):
    def setUp(self):
        (ROOT / 'tmp').mkdir(exist_ok=True)

    def test_portable_vbs_launches_only_its_sibling_fixture(self):
        source = (ROOT / 'Hermes_Local_Tools.vbs').read_text(encoding='utf-8')
        self.assertNotRegex(source, r'(?i)[a-z]:[\\/]')
        with tempfile.TemporaryDirectory(prefix="vbs 'quoted' ", dir=ROOT / 'tmp') as folder:
            base = Path(folder)
            shutil.copyfile(ROOT / 'Hermes_Local_Tools.vbs', base / 'Hermes_Local_Tools.vbs')
            (base / 'start.ps1').write_text(
                "param([switch]$IfNotRunning)\nif (-not $IfNotRunning) { exit 1 }\n"
                "[IO.File]::WriteAllText((Join-Path $PSScriptRoot 'started'), 'fixture')", encoding='utf-8')
            result = subprocess.run(['cscript.exe', '//B', '//Nologo', str(base / 'Hermes_Local_Tools.vbs')],
                env={**os.environ, 'HERMES_MCP_PWSH': str(PWSH)}, capture_output=True,
                timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline and not (base / 'started').exists():
                time.sleep(.1)
            self.assertEqual((base / 'started').read_text(), 'fixture')

    def test_missing_prerequisites_do_not_dispatch(self):
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as folder:
            target = Path(folder) / 'start.ps1'
            shutil.copyfile(ROOT / 'start.ps1', target)
            result = subprocess.run([str(PWSH), '-NoProfile', '-File', str(target)],
                capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'Missing local prerequisite', result.stderr)
            self.assertFalse((Path(folder) / 'runtime' / 'tunnel.pid').exists())

    def test_worker_clears_overrides_absent_from_caller_payload(self):
        prefix = (ROOT / 'start.ps1').read_text(encoding='utf-8').split('$runtimeDirectory =', 1)[0]
        with tempfile.TemporaryDirectory(dir=ROOT / 'tmp') as folder:
            script = Path(folder) / 'environment.ps1'
            script.write_text(prefix + "\n@{node=$env:HERMES_MCP_NODE;pwsh=$env:HERMES_MCP_PWSH;"
                "python=$env:HERMES_MCP_PYTHON;pathMatches=($env:PATH -eq $env:HERMES_EXPECTED_PATH);"
                "marker=$env:HERMES_UNRELATED_MARKER} | ConvertTo-Json -Compress", encoding='utf-8')
            payload = {'PATH': os.environ['PATH']}
            encoded = base64.b64encode(json.dumps(payload).encode()).decode()
            inherited = {**os.environ, 'HERMES_MCP_NODE': 'stale-node', 'HERMES_MCP_PWSH': 'stale-pwsh',
                         'HERMES_MCP_PYTHON': 'stale-python', 'HERMES_UNRELATED_MARKER': 'kept',
                         'HERMES_EXPECTED_PATH': payload['PATH']}
            result = subprocess.run([str(PWSH), '-NoProfile', '-File', str(script), '-Worker',
                                     '-RuntimePaths', encoded], env=inherited, capture_output=True,
                                    text=True, encoding='utf-8', timeout=10,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), {'node': None, 'pwsh': None, 'python': None,
                                                        'pathMatches': True, 'marker': 'kept'})

    def test_tunnel_survives_origin_job_cleanup_and_rejects_duplicate(self):
        k = ctypes.WinDLL('kernel32', use_last_error=True)
        k.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        k.CreateJobObjectW.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        k.OpenProcess.restype = wintypes.HANDLE
        k.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        k.IsProcessInJob.argtypes = [wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)]
        k.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        k.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        job = k.CreateJobObjectW(None, None)
        self.assertTrue(job)
        child = tunnel_handle = None
        with tempfile.TemporaryDirectory(prefix="launcher 'quoted' ", dir=ROOT / 'tmp') as folder:
            base = Path(folder)
            live = base / 'runtime'
            live.mkdir()
            shutil.copyfile(ROOT / 'start.ps1', base / 'start.ps1')
            code = base / 'fixture.cs'
            code.write_text('class Fixture { static void Main(string[] args) { '
                'System.Console.WriteLine(args.Length); foreach(string arg in args) System.Console.WriteLine(arg); '
                'System.Console.WriteLine(System.Environment.GetEnvironmentVariable("HERMES_MCP_NODE") ?? "missing"); '
                'System.Threading.Thread.Sleep(60000); } }')
            subprocess.run([str(CSC), '/nologo', '/target:exe', f'/out:{live / "tunnel-client.exe"}', str(code)],
                           check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            (live / 'hermes-local.yaml').write_text('fixture: no network\n')
            key = live / 'control-plane-key.dpapi'
            env = {**os.environ, 'HERMES_TEST_KEY_PATH': str(key), 'HERMES_MCP_NODE': str(base / 'fixture node.exe')}
            encoded = subprocess.run([str(PWSH), '-NoProfile', '-Command',
                "ConvertTo-SecureString 'dummy-test-only' -AsPlainText -Force | ConvertFrom-SecureString | Set-Content -LiteralPath $env:HERMES_TEST_KEY_PATH"],
                env=env, capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
            self.assertEqual(encoded.returncode, 0, encoded.stderr)
            gate = base / 'go'
            env.update(HERMES_TEST_GATE=str(gate), HERMES_TEST_START=str(base / 'start.ps1'))
            try:
                child = subprocess.Popen([str(PWSH), '-NoProfile', '-Command',
                    'while (-not (Test-Path -LiteralPath $env:HERMES_TEST_GATE)) { Start-Sleep -Milliseconds 50 }; & $env:HERMES_TEST_START; Start-Sleep -Seconds 60'],
                    env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW)
                parent_handle = k.OpenProcess(0x0101, False, child.pid)
                try:
                    self.assertTrue(k.AssignProcessToJobObject(job, parent_handle), ctypes.get_last_error())
                finally:
                    k.CloseHandle(parent_handle)
                gate.touch()
                pid_path = live / 'tunnel.pid'
                deadline = time.monotonic() + 25
                while time.monotonic() < deadline and not pid_path.exists():
                    time.sleep(.1)
                self.assertTrue(pid_path.exists(), 'fixture Tunnel did not start')
                tunnel_pid = int(pid_path.read_text().strip())
                tunnel_handle = k.OpenProcess(0x101001, False, tunnel_pid)
                self.assertTrue(tunnel_handle)
                output_path = live / 'tunnel.stdout.log'
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline:
                    lines = output_path.read_text().splitlines() if output_path.exists() else []
                    if len(lines) >= 5:
                        break
                    time.sleep(.1)
                self.assertEqual(lines, ['3', 'run', '--profile-file', str(live / 'hermes-local.yaml'), env['HERMES_MCP_NODE']])
                assigned = wintypes.BOOL()
                self.assertTrue(k.IsProcessInJob(tunnel_handle, job, ctypes.byref(assigned)))
                self.assertFalse(assigned.value, 'Tunnel inherited the originating desktop job')
                self.assertTrue(k.TerminateJobObject(job, 0))
                child.wait(timeout=5)
                self.assertEqual(k.WaitForSingleObject(tunnel_handle, 200), 258, 'Tunnel died with its origin')
                duplicate = subprocess.run([str(PWSH), '-NoProfile', '-File', str(base / 'start.ps1')],
                    capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertNotEqual(duplicate.returncode, 0)
                self.assertIn(b'already running', duplicate.stderr)
                self.assertEqual(int(pid_path.read_text().strip()), tunnel_pid)
                automatic = subprocess.run([str(PWSH), '-NoProfile', '-File', str(base / 'start.ps1'), '-IfNotRunning'],
                    capture_output=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(automatic.returncode, 0, automatic.stderr)
                self.assertEqual(int(pid_path.read_text().strip()), tunnel_pid)
                pid_path.unlink()
                race = subprocess.run([str(PWSH), '-NoProfile', '-Command',
                    '$global:queries = 0; function global:Get-CimInstance { param($ClassName, $Filter) '
                    '$global:queries++; if ($global:queries -gt 1) { CimCmdlets\\Get-CimInstance @PSBoundParameters } }; '
                    '& $env:HERMES_TEST_START -IfNotRunning'],
                    env=env, capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
                self.assertEqual(race.returncode, 0, race.stderr)
                self.assertEqual(k.WaitForSingleObject(tunnel_handle, 0), 258)
                subprocess.run([str(PWSH), '-NoProfile', '-Command',
                    'Get-CimInstance Win32_Process -Filter "Name = \'pwsh.exe\'" | '
                    'Where-Object { $_.CommandLine.Contains($env:HERMES_TEST_START) -and $_.CommandLine.Contains(" -Worker") } | '
                    'ForEach-Object { $p = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue; '
                    'if ($p -and -not $p.WaitForExit(10000)) { throw "Fixture worker did not exit" } }'],
                    env=env, check=True, capture_output=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW)
            finally:
                k.TerminateJobObject(job, 0)
                k.CloseHandle(job)
                if child is not None:
                    child.wait(timeout=5)
                if tunnel_handle:
                    k.TerminateProcess(tunnel_handle, 0)
                    k.WaitForSingleObject(tunnel_handle, 5000)
                    k.CloseHandle(tunnel_handle)
                else:
                    subprocess.run([str(PWSH), '-NoProfile', '-Command',
                        "Get-CimInstance Win32_Process -Filter \"Name = 'tunnel-client.exe'\" | Where-Object { $_.ExecutablePath -eq $env:HERMES_TEST_BINARY } | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction Stop }"],
                        env={**os.environ, 'HERMES_TEST_BINARY': str(live / 'tunnel-client.exe')},
                        capture_output=True, check=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)


if __name__ == '__main__':
    unittest.main()
