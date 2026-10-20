"""Resolve local executables without importing Hermes or reading private config."""
import os
import shutil
from pathlib import Path


def executable(name):
    setting = 'HERMES_MCP_' + name.upper()
    configured = os.environ.get(setting)
    if configured is not None:
        path = Path(configured)
        if not configured or not path.is_absolute() or not path.is_file():
            raise FileNotFoundError(f'{setting} must name an existing absolute executable path')
    else:
        found = shutil.which(name)
        if not found:
            raise FileNotFoundError(f'Install {name} on PATH or set {setting}')
        path = Path(found).resolve()
    if os.name == 'nt' and path.suffix.lower() not in ('.exe', '.com'):
        raise FileNotFoundError(f'{setting} must name a native executable, not a shell script')
    return path


def powershell():
    try:
        return executable('pwsh')
    except FileNotFoundError:
        if 'HERMES_MCP_PWSH' in os.environ:
            raise
        system = os.environ.get('SystemRoot')
        if system:
            fallback = Path(system) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
            if fallback.is_file():
                return fallback
        raise FileNotFoundError('Install PowerShell 7 on PATH or set HERMES_MCP_PWSH')
