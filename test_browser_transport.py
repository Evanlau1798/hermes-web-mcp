"""Real stdio image blocks survive text pagination; no real browser required."""
import asyncio
import base64
import json
import sys
import unittest

import runtime
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class BrowserTransportTests(unittest.TestCase):
    def test_image_is_delivered_once_separately_from_text(self):
        async def run():
            code = '''import asyncio, tempfile, threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import server
from backend import Bridge, process_registry
from browser_adapter import BrowserAdapter
with tempfile.TemporaryDirectory() as directory:
    def native(name, args, **context):
        if args.get('action') == 'log':
            return {'output':'繁體中文 ' + 'x'*8000, 'total_lines':1}
        return {'status':'exited','exit_code':0,'output_preview':'tail'}
    bridge = Bridge(native=native)
    bridge.browser = BrowserAdapter(Path(directory))
    folder = Path(directory)/'round'
    folder.mkdir()
    (folder/'screenshot.png').write_bytes(b'\\x89PNG\\r\\n\\x1a\\nfixture')
    bridge.browser.runs['owned'] = folder
    bridge.browser.sessions['owned'] = {}
    process_registry.get = Mock(return_value=SimpleNamespace(owner_task_id=bridge.owner,
        _lock=threading.Lock(),total_output_chars=8005,output_buffer='x'*8005,
        exited=True,completion_reason='exited'))
    server.Bridge = lambda: bridge
    asyncio.run(server.main())'''
            params = StdioServerParameters(command=sys.executable, args=['-c', code], cwd=runtime.ROOT,
                                           env={'PYTHONIOENCODING':'utf-8'})
            async with stdio_client(params) as streams:
                async with ClientSession(*streams) as client:
                    initialized = await client.initialize()
                    self.assertEqual(initialized.server_info.version, '0.1.1')
                    versions = json.loads((runtime.ROOT/'deploy/versions.json').read_text(encoding='utf-8'))
                    self.assertEqual(initialized.server_info.version, versions['webMcp'])
                    boot = await client.call_tool('hermes_context', {'task':'isolated image receipt fixture'})
                    bid = boot.structured_content['bootstrap']['bootstrap_id']
                    args = {'action':'log','session_id':'owned','bootstrap_id':bid}
                    logged = await client.call_tool('hermes_process_read', args)
                    self.assertEqual([block.type for block in logged.content], ['text'])
                    self.assertEqual(logged.structured_content['next_action']['arguments']['char_offset'], 6000)
                    args['char_offset'] = 6000
                    tail = await client.call_tool('hermes_process_read', args)
                    self.assertEqual([block.type for block in tail.content], ['text'])
                    self.assertEqual(tail.structured_content['next_action']['arguments']['action'], 'poll')
                    done = await client.call_tool('hermes_process_read', {
                        'action':'poll','session_id':'owned','bootstrap_id':bid})
                    self.assertEqual([block.type for block in done.content], ['text','image'])
                    self.assertTrue(base64.b64decode(done.content[1].data).startswith(b'\x89PNG'))
                    self.assertNotIn(done.content[1].data, str(done.structured_content))
                    self.assertEqual(done.structured_content['image_delivery']['state'], 'attached')
                    again = await client.call_tool('hermes_process_read', {
                        'action':'poll','session_id':'owned','bootstrap_id':bid})
                    self.assertEqual([block.type for block in again.content], ['text'])
                    self.assertEqual(again.structured_content['image_delivery']['state'], 'delivered_previously')
        asyncio.run(run())


if __name__ == '__main__':
    unittest.main()
