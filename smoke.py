"""Local MCP transport acceptance; changes only disposable files under tmp/."""
import asyncio
import json
import sys
from pathlib import Path

import runtime
import mcp_types as types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def main():
    folder = runtime.ROOT / 'tmp'
    folder.mkdir(exist_ok=True)
    target = folder / 'stdio-smoke.txt'
    parameters = StdioServerParameters(command=sys.executable, args=[str(runtime.ROOT / 'server.py')],
                                       cwd=runtime.ROOT, env={'PYTHONIOENCODING': 'utf-8'})
    try:
        async with stdio_client(parameters) as streams:
            async def approve(ctx, params):
                raise AssertionError('Direct execution must not request approval')
            async with ClientSession(*streams, elicitation_callback=approve) as client:
                await client.initialize()
                tools = (await client.list_tools()).tools
                assert len(tools) == 27
                assert next(t for t in tools if t.name == 'hermes_memory_read').annotations.read_only_hint
                process_read = next(t for t in tools if t.name == 'hermes_process_read')
                assert process_read.annotations.read_only_hint
                assert process_read.annotations.destructive_hint is False
                assert process_read.annotations.open_world_hint is False
                assert not next(t for t in tools if t.name == 'hermes_process_manage').annotations.read_only_hint
                bootstrap_id = None
                async def call(name, args):
                    nonlocal bootstrap_id
                    if name != 'hermes_context':
                        args = {**args, 'bootstrap_id':bootstrap_id}
                    reply = await client.call_tool(name, args, read_timeout_seconds=60)
                    assert not reply.is_error, str(reply)
                    data = json.loads(reply.content[0].text)
                    if name == 'hermes_context':
                        bootstrap_id = data['bootstrap']['bootstrap_id']
                    assert reply.structured_content == data, 'Web clients need structured results as well as text'
                    return data
                overview = await call('hermes_context', {'task':'isolated stdio smoke'})
                assert len(overview['tools']) == 27 and overview['computer_use']['exposed']
                assert overview['processes']['read'] == 'hermes_process_read'
                skills = await call('hermes_skills_list', {'limit': 1})
                assert skills['total'] > 0
                skill = await call('hermes_skill_read', {'skill_id': skills['skills'][0]['id']})
                assert skill['content'] and skill['preprocessed'] is False
                await call('hermes_write_file', {'path': str(target), 'content': '繁體中文 English'})
                data = await call('hermes_read_file', {'path': str(target)})
                assert '繁體中文 English' in data['content']
                result = await call('hermes_terminal', {
                    'command': "Write-Output '繁體中文 English'",
                    'workdir': str(folder), 'timeout': 10})
                assert result['exit_code'] == 0 and '繁體中文 English' in result['output'], result
                result = await call('hermes_terminal', {
                    'command': 'Start-Sleep -Seconds 90',
                    'workdir': str(folder), 'background': True})
                session_id = result['session_id']
                listed = await call('hermes_process_read', {'action': 'list'})
                assert session_id in [p['session_id'] for p in listed['processes']], listed
                polled = await call('hermes_process_read', {'action': 'poll', 'session_id': session_id})
                assert polled['status'] == 'running', polled
                logged = await call('hermes_process_read', {'action': 'log', 'session_id': session_id, 'limit': 10})
                assert 'output' in logged, logged
                denied = await client.call_tool('hermes_process_read', {'action': 'kill', 'session_id': session_id, 'bootstrap_id':bootstrap_id})
                assert denied.is_error, denied
                foreign = await client.call_tool('hermes_process_read', {'action': 'poll', 'session_id': 'foreign', 'bootstrap_id':bootstrap_id})
                assert foreign.is_error, foreign
                legacy = await call('hermes_process_manage', {'action': 'poll', 'session_id': session_id})
                assert legacy['status'] == 'running', legacy
                killed = await call('hermes_process_manage', {'action': 'kill', 'session_id': session_id})
                assert killed.get('status') != 'running', killed
                print('PASS: 27 stdio tools, readonly metadata, UTF-8, PowerShell, owned list/poll/log, rejected kill/foreign, legacy poll/kill')
    finally:
        target.unlink(missing_ok=True)


if __name__ == '__main__':
    asyncio.run(main())
