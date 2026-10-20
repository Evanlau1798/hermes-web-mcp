#requires -Version 7.0
param([string]$FromDotEnv)
$ErrorActionPreference = 'Stop'
$keyPath = Join-Path $PSScriptRoot 'runtime\control-plane-key.dpapi'
$runtimeDirectory = Split-Path -Parent $keyPath
New-Item -ItemType Directory -Path $runtimeDirectory -Force | Out-Null
if ($FromDotEnv) {
    $entries = @(Get-Content -LiteralPath $FromDotEnv | Where-Object { $_ -match '^OPENAI_TUNNEL_API_KEY\s*=' })
    if ($entries.Count -ne 1) { throw 'Expected exactly one OPENAI_TUNNEL_API_KEY entry.' }
    $plain = ($entries[0] -split '=', 2)[1].Trim()
    if ($plain.Length -ge 2 -and (($plain.StartsWith('"') -and $plain.EndsWith('"')) -or ($plain.StartsWith("'") -and $plain.EndsWith("'")))) {
        $plain = $plain.Substring(1, $plain.Length - 2)
    }
    if (-not $plain) { throw 'Empty key was not saved.' }
    $key = ConvertTo-SecureString -String $plain -AsPlainText -Force
    $plain = $null
    $entries = $null
} else {
    $key = Read-Host 'Enter the restricted Tunnel runtime API key (hidden)' -AsSecureString
}
if ($key.Length -eq 0) { throw 'Empty key was not saved.' }
$key | ConvertFrom-SecureString | Set-Content -LiteralPath $keyPath -Encoding ASCII
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$acl = New-Object Security.AccessControl.FileSecurity
$acl.SetAccessRuleProtection($true, $false)
$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'Allow')))
$acl.AddAccessRule((New-Object Security.AccessControl.FileSystemAccessRule('SYSTEM', 'FullControl', 'Allow')))
Set-Acl -LiteralPath $keyPath -AclObject $acl
Write-Host 'Saved with Windows DPAPI protection for the current Windows user.'
