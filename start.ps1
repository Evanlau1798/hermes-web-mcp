#requires -Version 7.0
param([switch]$Worker, [switch]$IfNotRunning, [string]$RuntimePaths)
$ErrorActionPreference = 'Stop'
$runtimeNames = @('PATH', 'HERMES_MCP_NODE', 'HERMES_MCP_PWSH', 'HERMES_MCP_PYTHON')
if ($Worker -and $RuntimePaths) {
    $paths = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($RuntimePaths)) | ConvertFrom-Json
    foreach ($name in $runtimeNames) {
        if ($paths.PSObject.Properties.Name -contains $name) {
            [Environment]::SetEnvironmentVariable($name, [string]$paths.$name, 'Process')
        } else {
            # Missing means unset in the caller, not inherit the WMI host's old override.
            Remove-Item -LiteralPath "Env:$name" -ErrorAction SilentlyContinue
        }
    }
}
$runtimeDirectory = Join-Path $PSScriptRoot 'runtime'
$binary = Join-Path $runtimeDirectory 'tunnel-client.exe'
$profile = Join-Path $runtimeDirectory 'hermes-local.yaml'
$keyPath = Join-Path $runtimeDirectory 'control-plane-key.dpapi'
foreach ($file in @($binary, $profile, $keyPath)) {
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing local prerequisite: $file" }
}
$existing = Get-CimInstance Win32_Process -Filter "Name = 'tunnel-client.exe'" |
    Where-Object { $_.ExecutablePath -eq $binary }
if ($existing) {
    if ($IfNotRunning) { Write-Host "Hermes tunnel already running, PID $($existing.ProcessId)."; return }
    throw 'The Hermes tunnel-client is already running; a second stdio instance is not supported.'
}
if (-not $Worker) {
    # WMI owns the launcher so desktop job cleanup cannot terminate the Tunnel.
    # Its environment differs from the caller; forward only executable settings, never keys.
    $paths = @{}
    foreach ($name in $runtimeNames) {
        $value = [Environment]::GetEnvironmentVariable($name, 'Process')
        if ($null -ne $value) { $paths[$name] = $value }
    }
    $encodedPaths = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($paths | ConvertTo-Json -Compress)))
    $started = Get-Date
    $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ShowWindow = [uint16]0}
    $launch = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = ('"{0}" -NoProfile -NonInteractive -File "{1}" -Worker -RuntimePaths {2}{3}' -f (Join-Path $PSHOME 'pwsh.exe'), $PSCommandPath, $encodedPaths, $(if ($IfNotRunning) { ' -IfNotRunning' } else { '' }))
        CurrentDirectory = $PSScriptRoot
        ProcessStartupInformation = $startup
    }
    if ($launch.ReturnValue -ne 0) { throw "Detached launcher failed, WMI code $($launch.ReturnValue)." }
    $deadline = $started.AddSeconds(20)
    do {
        $candidate = Get-CimInstance Win32_Process -Filter "Name = 'tunnel-client.exe'" |
            Where-Object { $_.ExecutablePath -eq $binary -and ($IfNotRunning -or $_.CreationDate -ge $started.AddSeconds(-1)) }
        if ($IfNotRunning -and $candidate) {
            Write-Host "Hermes tunnel running, PID $($candidate.ProcessId). Verify readiness before using it."
            return
        }
        $pidPath = Join-Path $runtimeDirectory 'tunnel.pid'
        if ($candidate -and (Test-Path -LiteralPath $pidPath) -and
            (Get-Content -LiteralPath $pidPath -Raw).Trim() -eq [string]$candidate.ProcessId) {
            Write-Host "Hermes tunnel launched independently of Codex, PID $($candidate.ProcessId). Verify readiness before using it."
            return
        }
        Start-Sleep -Milliseconds 200
    } while ((Get-Date) -lt $deadline)
    throw 'Detached startup was not confirmed within 20 seconds; inspect runtime/launcher.error.log and process state before retrying.'
}
$mutexName = 'Local\HermesTunnelLaunch-' + [Convert]::ToHexString([System.Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes($binary.ToLowerInvariant())))
$mutex = [Threading.Mutex]::new($false, $mutexName)
$locked = $false
try {
    try { $locked = $mutex.WaitOne(5000) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'Another Tunnel launch is in progress.' }
    $existing = Get-CimInstance Win32_Process -Filter "Name = 'tunnel-client.exe'" |
        Where-Object { $_.ExecutablePath -eq $binary }
    if ($existing) {
        if ($IfNotRunning) { return }
        throw 'The Hermes tunnel-client is already running.'
    }
    $secure = (Get-Content -LiteralPath $keyPath -Raw).Trim() | ConvertTo-SecureString
    $credential = New-Object System.Net.NetworkCredential('', $secure)
    $previousKey = $env:CONTROL_PLANE_API_KEY
    try {
        $env:CONTROL_PLANE_API_KEY = $credential.Password
        $env:PYTHONIOENCODING = 'utf-8'
        $process = Start-Process -FilePath $binary -ArgumentList @('run', '--profile-file', ('"{0}"' -f $profile)) `
            -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $runtimeDirectory 'tunnel.stdout.log') `
            -RedirectStandardError (Join-Path $runtimeDirectory 'tunnel.stderr.log')
        $process.Id | Set-Content -LiteralPath (Join-Path $runtimeDirectory 'tunnel.pid') -Encoding ASCII
        Write-Host "Hermes tunnel launched, PID $($process.Id). Verify readiness before using it."
    } finally {
        $env:CONTROL_PLANE_API_KEY = $previousKey
        $credential = $null
        $secure = $null
    }
} catch {
    [IO.File]::WriteAllText((Join-Path $runtimeDirectory 'launcher.error.log'), "Detached startup failed: $($_.Exception.GetType().Name)")
    throw
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
