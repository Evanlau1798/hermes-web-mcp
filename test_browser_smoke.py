"""Failure cleanup of the opt-in smoke never races unconfirmed work."""
import asyncio
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch
import browser_smoke


class SmokeCleanupTests(unittest.TestCase):
    def run_failure(self, *, unknown):
        calls = []
        class Client:
            async def initialize(self):
                return SimpleNamespace(server_info=SimpleNamespace(version='fixture'))
            async def list_tools(self):
                return SimpleNamespace(tools=[None]*28)
            async def call_tool(self, name, args, **_):
                if name == 'hermes_context':
                    data = {'bootstrap':{'bootstrap_id':'fixture'}}
                else:
                    code = args['code']
                    calls.append(code)
                    if 'finish(' in code:
                        data = {'status':'exited','exit_code':0,'output':'cleaned'}
                    elif 'page.goto' in code:
                        if unknown:
                            raise ConnectionError('transport outcome unknown')
                        data = {'status':'exited','exit_code':1,'output':'navigation failed'}
                    else:
                        data = {'status':'exited','exit_code':0,
                            'output':'WITNESS {"spaceId":42,"phase":"created"}'}
                return SimpleNamespace(is_error=False,structured_content=data,content=[])
        @asynccontextmanager
        async def transport(*_):
            yield (None,None)
        @asynccontextmanager
        async def session(*_):
            yield Client()
        with patch.object(browser_smoke,'stdio_client',transport), \
             patch.object(browser_smoke,'ClientSession',session):
            with self.assertRaises((AssertionError,ConnectionError)):
                asyncio.run(browser_smoke.acceptance('http://fixture.invalid/'))
        return calls

    def test_known_navigation_exit_cleans_created_space(self):
        self.assertTrue(any('finish(' in c for c in self.run_failure(unknown=False)))

    def test_unknown_execution_never_dispatches_cleanup(self):
        self.assertFalse(any('finish(' in c for c in self.run_failure(unknown=True)))


if __name__ == '__main__':
    unittest.main()
