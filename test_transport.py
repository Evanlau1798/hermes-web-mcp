"""A single fresh stdio probe: two Web sessions sharing one transport."""
import asyncio
import json
import sys
import unittest

import runtime
import mcp_types as types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class TransportTests(unittest.TestCase):
    def test_codex_failure_diagnostics_survive_real_stdio_transport(self):
        async def run():
            code = '''import asyncio, server
from backend import Bridge
server.Bridge=lambda: Bridge(codex=lambda *_: {
    "ok":False,"errorCode":"THREAD_NOT_LOADED","stage":"read",
    "requestId":"fixture-request","source":"codex-desktop-live","tool":"read_thread",
    "requestState":"rejected","deliveryState":"failed","timedOut":False,"disconnected":False,
    "automaticRetrySafe":True,"error":"Desktop 回報 notLoaded，不能視為閒置。"})
asyncio.run(server.main())'''
            parameters = StdioServerParameters(command=sys.executable, args=['-c', code], cwd=runtime.ROOT,
                                               env={'PYTHONIOENCODING': 'utf-8'})
            async with stdio_client(parameters) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    await client.call_tool('hermes_context', {})
                    result = await client.call_tool('codex_status', {'thread_id': '11111111-1111-4111-8111-111111111111'})
                    self.assertTrue(result.is_error)
                    self.assertEqual(result.structured_content['stage'], 'read')
                    self.assertEqual(result.structured_content['errorCode'], 'THREAD_NOT_LOADED')
                    self.assertEqual(result.structured_content['requestId'], 'fixture-request')
                    self.assertIn('notLoaded', result.content[0].text)
        asyncio.run(run())

    def test_bootstrap_metadata_isolation_and_passive_read(self):
        async def run():
            parameters = StdioServerParameters(command=sys.executable,
                args=[str(runtime.ROOT / 'server.py')], cwd=runtime.ROOT,
                env={'PYTHONIOENCODING': 'utf-8'})
            async with stdio_client(parameters) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    catalog = (await client.list_tools()).tools
                    self.assertEqual(len(catalog), 27)
                    self.assertEqual(next(t for t in catalog if t.name == 'browser_use').title, 'browser use')
                    reading = next(t for t in catalog if t.name == 'hermes_process_read')
                    self.assertTrue(reading.annotations.read_only_hint)
                    self.assertFalse(reading.annotations.destructive_hint)
                    self.assertEqual(reading.input_schema['properties']['action']['enum'], ['list', 'poll', 'log'])
                    a = types.RequestParamsMeta(**{'openai/session': 'transport-a'})
                    b = types.RequestParamsMeta(**{'openai/session': 'transport-b'})
                    args = {'path': str(runtime.ROOT / '.gitignore')}
                    blocked = await client.call_tool('hermes_read_file', args, meta=a)
                    self.assertTrue(blocked.is_error)
                    self.assertEqual(blocked.structured_content['errorCode'], 'bootstrap_required')
                    bootstrap = await client.call_tool('hermes_context', {'cwd': str(runtime.ROOT)}, meta=a)
                    self.assertFalse(bootstrap.is_error)
                    self.assertEqual(bootstrap.structured_content['bootstrap']['terminal_default'], 'powershell')
                    process_list = await client.call_tool('hermes_process_read', {'action': 'list'}, meta=a)
                    self.assertFalse(process_list.is_error)
                    self.assertEqual(process_list.structured_content['processes'], [])
                    denied_control = await client.call_tool('hermes_process_read', {'action': 'kill', 'session_id': 'unused'}, meta=a)
                    self.assertTrue(denied_control.is_error)
                    other = await client.call_tool('hermes_read_file', args, meta=b)
                    self.assertTrue(other.is_error)
                    read = await client.call_tool('hermes_read_file', args, meta=a)
                    self.assertFalse(read.is_error)
                    self.assertEqual(json.loads(read.content[0].text), read.structured_content)
                    terminal = await client.call_tool('hermes_terminal', {'command': "Write-Output '繁體成功'; Write-Error 'expected-error'", 'timeout': 10}, meta=a)
                    self.assertFalse(terminal.is_error)
                    self.assertEqual(terminal.structured_content['exit_code'], 1)
                    self.assertEqual(terminal.structured_content['shell'], 'powershell')
                    self.assertIn('繁體成功', terminal.structured_content['output'])
                    self.assertEqual(terminal.structured_content['outcome'], 'shell_exit')
                    self.assertFalse(terminal.structured_content['timed_out'])
                    self.assertEqual(terminal.structured_content['stdout'], terminal.structured_content['output'])
                    self.assertIsNone(terminal.structured_content['stderr'])
                    self.assertFalse(terminal.structured_content['automaticRetrySafe'])
                    legacy = await client.call_tool('hermes_read_file', args)
                    self.assertTrue(legacy.is_error)
                    await client.call_tool('hermes_context', {})
                    legacy = await client.call_tool('hermes_read_file', args)
                    self.assertFalse(legacy.is_error)
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
