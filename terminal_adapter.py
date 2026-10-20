"""Prepare arguments for Hermes' native terminal; never execute a process here."""

import shlex
import re
from local_paths import powershell


def terminal_receipt(result):
    """A command exit is data; retain native merged output and execution failures."""
    result = dict(result)
    output = result.get('output', '')
    partial = bool(result.get('truncated') or result.get('output_truncated') or result.get('truncation_note'))
    native_timeout = result.get('exit_code') == 124 and isinstance(output, str) and bool(
        re.search(r'\[Command timed out after [0-9.]+s\]\s*$', output))
    if isinstance(output, str) and len(output) > 20000:
        output = output[:10000] + '\n…（輸出已截短）…\n' + output[-10000:]
        partial = True
    # Native terminal merges streams; do not invent a separate stderr channel.
    result.update(output=output, stdout=output, stderr=None, streams_merged=True,
                  partial_output=partial, automaticRetrySafe=False)
    error = result.get('error')
    result['timed_out'] = bool(result.get('hermes_timed_out') or native_timeout or
                               (error and 'timed out' in str(error).casefold()))
    result['outcome'] = ('execution_error' if error else 'shell_exit'
                         if isinstance(result.get('exit_code'), int) else 'running')
    return result


def prepare_terminal(arguments, *, default_shell='powershell', session_key=''):
    """Return (native_arguments, {'shell': ..., 'executable': ...}).

    Call after public schema validation (native schema plus optional shell enum),
    Load runtime first, then dispatch native_arguments through the unchanged
    Hermes terminal handler with its existing task/session ownership and guards.
    Pass the same native session/task key as session_key for original-script
    preflight guards. PowerShell requires the local backend; no process is
    launched by this adapter. Native guard rejection raises ValueError.
    Use default_shell='bash' for legacy bootstrap sessions; an explicit shell wins.
    Bash is unchanged. PowerShell runs as a child of native Bash: UTF-8 output,
    explicit exit wins, otherwise last native nonzero exit wins, otherwise any
    PowerShell error gives exit 1. Child cwd/environment changes do not persist.
    Other parameters, including background/PTY/notifications, pass through.
    Invalid command/shell raises ValueError; no installed PowerShell raises
    FileNotFoundError. The input dictionary is not modified.
    """
    if not isinstance(arguments, dict):
        raise ValueError('terminal arguments must be an object')
    shell = arguments.get('shell', default_shell)
    if default_shell not in ('powershell', 'bash') or shell not in ('powershell', 'bash'):
        raise ValueError('shell must be powershell or bash')
    command = arguments.get('command')
    if not isinstance(command, str) or not command.strip() or '\0' in command:
        raise ValueError('command must be a nonempty string without NUL')
    native = dict(arguments)
    native.pop('shell', None)
    if shell == 'bash':
        return native, {'shell': shell, 'executable': None}

    executable = powershell()

    _guard_original(command, native, session_key)

    # Keep the program plaintext: native guards cannot inspect EncodedCommand.
    script = (
        '[Console]::InputEncoding = [Console]::OutputEncoding = '
        '$OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n'
        '$global:LASTEXITCODE = 0\n'
        '$Error.Clear()\n'
        '& {\n' + command + '\n}\n'
        '$hermesCommandSucceeded = $?\n'
        'if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }\n'
        'if (-not $hermesCommandSucceeded -or $Error.Count -gt 0) { exit 1 }\n'
        'exit 0'
    )
    path = executable.as_posix()
    native['command'] = shlex.join([path, '-NoLogo', '-NoProfile', '-NonInteractive', '-Command', script])
    return native, {'shell': shell, 'executable': path}


def _guard_original(command, arguments, session_key):
    from agent.deadline import run_bounded_sync
    from tools import terminal_tool as terminal
    from tools.approval import get_current_session_key

    config = terminal._get_env_config()
    if config['env_type'] != 'local':
        raise ValueError('Windows PowerShell adapter requires the native local backend')
    key = session_key or get_current_session_key(default='')
    overrides = terminal.resolve_task_overrides(key)
    cwd = overrides.get('cwd') or terminal.get_session_cwd(key) or config['cwd']
    try:
        checked = run_bounded_sync(
            lambda: terminal._pre_exec_block(command, env=None, env_type='local',
                                             cwd=cwd, workdir=arguments.get('workdir'), session_key=key),
            max(arguments.get('timeout') or 180, terminal._PRE_EXEC_GUARD_MIN_TIMEOUT_S),
            label='terminal-adapter.pre-exec-guard',
        )
    except terminal._Rejected as rejection:
        raise ValueError(rejection.result_json) from rejection
    if checked.timed_out:
        raise ValueError('Native original-script pre-exec guard timed out; command was not prepared')
    verdict = terminal._check_all_guards(command, 'local')
    if not verdict.get('approved'):
        raise ValueError(verdict.get('message') or 'Native original-script guard rejected the command')
