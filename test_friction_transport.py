import asyncio
import sys
import unittest
import runtime
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client


class FrictionTransportTests(unittest.TestCase):
    def test_tracking_wait_and_error_metadata_over_real_stdio(self):
        async def run():
            code='''import asyncio, server
from backend import Bridge
ID='11111111-1111-4111-8111-111111111111'
b=Bridge(codex=lambda *_:{'thread':{'id':ID,'status':{'type':'active'}},'latestTurn':{'id':'turn1','status':'inProgress'}},
 native=lambda *_a,**_kw:{'error':'IMPORTANT_FAILURE','errorCode':'FIXTURE','exit_code':1,'output':'x'*100000})
server.Bridge=lambda:b
asyncio.run(server.main())'''
            p=StdioServerParameters(command=sys.executable,args=['-c',code],cwd=runtime.ROOT,env={'PYTHONIOENCODING':'utf-8'})
            async with stdio_client(p) as streams:
                async with ClientSession(*streams) as c:
                    await c.initialize()
                    await c.call_tool('hermes_context',{})
                    start=await c.call_tool('hermes_tracking',{'action':'start','kind':'codex',
                        'thread_id':'11111111-1111-4111-8111-111111111111'})
                    s=start.structured_content
                    self.assertTrue(s['baseline_ready'])
                    waited=await c.call_tool('hermes_tracking_status',{'tracking_id':s['tracking_id'],
                        'after_sequence':s['latest_sequence'],'wait_seconds':0.02})
                    self.assertEqual(waited.structured_content['wait']['reason'],'timeout')
                    self.assertTrue(waited.structured_content['alive'])
                    error=await c.call_tool('hermes_read_file',{'path':'fixture-only'})
                    self.assertTrue(error.is_error)
                    self.assertEqual(error.structured_content['errorCode'],'FIXTURE')
                    self.assertEqual(error.structured_content['error'],'IMPORTANT_FAILURE')
                    stopped=await c.call_tool('hermes_tracking',{'action':'stop','tracking_id':s['tracking_id']})
                    self.assertFalse(stopped.structured_content['alive'])
        asyncio.run(run())


if __name__=='__main__':
    unittest.main()
