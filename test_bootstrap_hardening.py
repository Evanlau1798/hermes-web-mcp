import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import discovery
import runtime
from backend import Bridge


class HardenedBootstrapTests(unittest.TestCase):
    def test_shared_transport_cannot_inherit_another_metadata_free_bootstrap(self):
        import asyncio
        import sys
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def run():
            code = "import asyncio,server; from backend import Bridge; server.Bridge=lambda:Bridge(native=lambda *a,**k:{'content':'fixture'}); asyncio.run(server.main())"
            p = StdioServerParameters(command=sys.executable, args=['-c',code], cwd=runtime.ROOT,
                                      env={'PYTHONIOENCODING':'utf-8'})
            async with stdio_client(p) as streams:
                async with ClientSession(*streams) as client:
                    await client.initialize()
                    await client.call_tool('hermes_context', {'task':'chat A initialization'})
                    result = await client.call_tool('hermes_read_file', {'path':'fixture'})
                    self.assertTrue(result.is_error, 'a metadata-free call must not inherit chat A bootstrap')
                    self.assertEqual(result.structured_content['errorCode'], 'bootstrap_required')
        asyncio.run(run())

    def setUp(self):
        self.native = Mock(return_value={'content': '1|rules', 'total_lines': 1, 'truncated': False})
        self.bridge = Bridge(native=self.native)
        self.addCleanup(self.bridge.close)

    def call(self, name, args, scope=None):
        return self.bridge.call(name, args, client_scope=scope, require_bootstrap=True)

    def test_no_metadata_chats_do_not_inherit_transport_or_each_other(self):
        a = self.call('hermes_context', {'task': 'read file'})
        self.assertEqual(self.call('hermes_read_file', {'path': 'fixture'})['errorCode'], 'bootstrap_required')
        aid = a['bootstrap']['bootstrap_id']
        b = self.call('hermes_context', {'task': 'second independent task'})
        self.assertNotEqual(aid, b['bootstrap']['bootstrap_id'])
        self.call('hermes_read_file', {'path': 'fixture', 'bootstrap_id': aid})
        self.assertEqual(self.native.call_count, 1)
        self.assertEqual(a['bootstrap']['session_tracking'], 'explicit-bootstrap')

    def test_metadata_binding_and_task_reinitialization_invalidate_old_id(self):
        a = self.call('hermes_context', {'task': 'first task'}, 'chat-a')
        aid = a['bootstrap']['bootstrap_id']
        args = {'path': 'fixture', 'bootstrap_id': aid}
        self.assertEqual(self.call('hermes_read_file', args, 'chat-b')['errorCode'], 'bootstrap_required')
        self.assertEqual(self.call('hermes_read_file', args)['errorCode'], 'bootstrap_required')
        b = self.call('hermes_context', {'task': 'new task', 'bootstrap_id': aid}, 'chat-a')
        self.assertNotEqual(aid, b['bootstrap']['bootstrap_id'])
        self.assertEqual(self.call('hermes_read_file', args, 'chat-a')['errorCode'], 'bootstrap_stale')
        self.assertEqual(self.native.call_count, 0)

    def test_explicit_reinitialization_rotates_token_and_validates_before_reset(self):
        a = self.call('hermes_context', {'task': 'first task'})['bootstrap']['bootstrap_id']
        with self.assertRaises(ValueError):
            self.call('hermes_context', {'task': 'invalid cwd', 'cwd': 'not-absolute', 'bootstrap_id': a})
        self.call('hermes_read_file', {'path': 'fixture', 'bootstrap_id': a})
        b = self.call('hermes_context', {'task': 'new task', 'bootstrap_id': a})['bootstrap']['bootstrap_id']
        self.assertNotEqual(a, b)
        self.assertEqual(self.call('hermes_read_file', {'path': 'fixture', 'bootstrap_id': a})['errorCode'], 'bootstrap_required')

    def test_prerequisites_gate_project_instructions_and_reset_on_new_task(self):
        with tempfile.TemporaryDirectory(dir=runtime.ROOT/'tmp') as folder:
            root = Path(folder)
            (root/'AGENTS.md').write_text('rules', encoding='utf-8')
            with patch.object(discovery, 'SKILLS_ROOT', root/'skills'), patch.object(discovery, 'CODEX_ROOTS', []):
                start = self.call('hermes_context', {'cwd': folder, 'task': 'deploy project'}, 'chat')
                bid = start['bootstrap']['bootstrap_id']
                self.assertFalse(start['bootstrap']['execution_ready'])
                self.assertEqual(start['prerequisites']['skill_match_status'], 'no_match')
                self.assertEqual(start['prerequisites']['pending'][0]['kind'], 'project_instruction')
                denied = self.call('hermes_write_file', {'path': str(root/'out'), 'content':'x', 'bootstrap_id':bid}, 'chat')
                self.assertEqual(denied['errorCode'], 'instructions_read_required')
                self.assertEqual(self.native.call_count, 0)
                self.call('hermes_skills_list', {'query':'deploy project','bootstrap_id':bid}, 'chat')
                read = self.call('hermes_read_file', {'path':str(root/'AGENTS.md'), 'bootstrap_id':bid}, 'chat')
                self.assertTrue(read['prerequisites']['execution_ready'])
                self.call('hermes_write_file', {'path':str(root/'out'), 'content':'x', 'bootstrap_id':bid}, 'chat')
                self.assertEqual(self.native.call_count, 2)
                reset = self.call('hermes_context', {'cwd':folder, 'task':'second task', 'bootstrap_id':bid}, 'chat')
                self.assertFalse(reset['bootstrap']['execution_ready'])
                self.assertTrue(reset['prerequisites']['pending'])

    def test_harness_first_instructions_and_public_contract(self):
        import server
        specs = server.public_specs(self.bridge.specs)
        for name, spec in specs.items():
            self.assertIn('bootstrap_id', spec['input_schema']['properties'])
            if name != 'hermes_context':
                self.assertIn('bootstrap_id', spec['input_schema']['required'])
        self.assertNotIn('bootstrap_id', self.bridge.specs['hermes_read_file']['input_schema']['properties'])
        result = self.call('hermes_context', {'task': 'read test'})
        self.assertEqual(result['harness']['steps'][0]['tool'], 'hermes_context')
        self.assertIn('hermes_skill_read', str(result['harness']))
        self.assertIn('Hermes Harness', server.INSTRUCTIONS[:300])
        self.assertFalse(result['execution']['model_started'])
        self.assertFalse(result['execution']['persona_loaded'])

    def test_instruction_pages_cannot_skip_or_accept_failed_and_outer_truncated_reads(self):
        with tempfile.TemporaryDirectory(dir=runtime.ROOT/'tmp') as folder:
            path = Path(folder)/'AGENTS.md'
            path.write_text('rules\n'*5, encoding='utf-8')
            start = self.call('hermes_context', {'cwd':folder,'task':'edit fixture'}, 'chat')
            bid = start['bootstrap']['bootstrap_id']
            def read(offset, value):
                self.native.return_value = value
                return self.call('hermes_read_file', {'path':str(path), 'offset':offset, 'limit':2, 'bootstrap_id':bid}, 'chat')
            self.assertFalse(read(5, {'content':'last','total_lines':5,'truncated':False})['prerequisites']['execution_ready'])
            self.assertFalse(read(1, {'content':'bad','total_lines':5,'truncated':False,'ok':False})['prerequisites']['execution_ready'])
            self.assertFalse(read(1, {'content':'X'*100000,'total_lines':5,'truncated':False})['prerequisites']['execution_ready'])
            self.assertFalse(read(1, {'content':'first','total_lines':5,'truncated':True})['prerequisites']['execution_ready'])
            self.assertFalse(read(3, {'content':'middle','total_lines':5,'truncated':True})['prerequisites']['execution_ready'])
            self.assertTrue(read(5, {'content':'last','total_lines':5,'truncated':False})['prerequisites']['execution_ready'])

    def test_required_skill_cannot_be_satisfied_from_another_project(self):
        with tempfile.TemporaryDirectory(dir=runtime.ROOT/'tmp') as folder:
            roots = [Path(folder)/name for name in ('a','b')]
            for root in roots:
                skill = root/'.agents/skills/deploy-service'
                skill.mkdir(parents=True)
                (root/'.git').mkdir()
                (skill/'SKILL.md').write_text('---\nname: deploy-service\ndescription: deploy service\n---\nrules', encoding='utf-8')
            with patch.object(discovery,'SKILLS_ROOT',Path(folder)/'none'), patch.object(discovery,'CODEX_ROOTS',[]):
                start = self.call('hermes_context', {'cwd':str(roots[0]),'task':'deploy service'}, 'chat')
                bid = start['bootstrap']['bootstrap_id']
                result = self.call('hermes_skill_read', {'skill_id':'project:deploy-service','cwd':str(roots[1]),'bootstrap_id':bid}, 'chat')
                self.assertEqual(result['errorCode'], 'bootstrap_cwd_changed')
                denied = self.call('hermes_write_file', {'path':str(roots[0]/'out'),'content':'x','bootstrap_id':bid}, 'chat')
                self.assertEqual(denied['errorCode'], 'skill_read_required')

    def test_unmatched_risk_requires_task_search_before_native_mutation(self):
        with tempfile.TemporaryDirectory(dir=runtime.ROOT/'tmp') as folder:
            with patch.object(discovery,'SKILLS_ROOT',Path(folder)/'none'), patch.object(discovery,'CODEX_ROOTS',[]):
                task = 'deploy service'
                start = self.call('hermes_context', {'cwd':folder,'task':task}, 'chat')
                bid = start['bootstrap']['bootstrap_id']
                self.assertFalse(start['bootstrap']['execution_ready'])
                self.assertEqual(start['prerequisites']['pending'][0]['kind'], 'skill_search')
                args = {'path':str(Path(folder)/'out'),'content':'x','bootstrap_id':bid}
                self.assertEqual(self.call('hermes_write_file',args,'chat')['errorCode'],'skill_search_required')
                self.assertEqual(self.native.call_count,0)
                self.call('hermes_skills_list', {'query':'unrelated','bootstrap_id':bid}, 'chat')
                self.assertEqual(self.call('hermes_write_file',args,'chat')['errorCode'],'skill_search_required')
                result = self.call('hermes_skills_list', {'query':task,'bootstrap_id':bid}, 'chat')
                self.assertTrue(result['prerequisites']['execution_ready'])
                self.assertEqual(result['prerequisites']['skill_match_status'],'searched_no_match')
                self.call('hermes_write_file',args,'chat')
                self.assertEqual(self.native.call_count,1)

    def test_search_new_match_requires_read_before_write(self):
        with tempfile.TemporaryDirectory(dir=runtime.ROOT/'tmp') as folder:
            (Path(folder)/'.git').mkdir()
            with patch.object(discovery,'SKILLS_ROOT',Path(folder)/'none'), patch.object(discovery,'CODEX_ROOTS',[]):
                start = self.call('hermes_context', {'cwd':folder,'task':'deploy service'}, 'chat')
                bid = start['bootstrap']['bootstrap_id']
                skill = Path(folder)/'.agents/skills/deploy-service'
                skill.mkdir(parents=True)
                (skill/'SKILL.md').write_text('---\nname: deploy-service\ndescription: deploy service\n---\nrules', encoding='utf-8')
                result = self.call('hermes_skills_list', {'query':'deploy service','bootstrap_id':bid}, 'chat')
                self.assertEqual(result['prerequisites']['pending'][0]['skill_id'], 'project:deploy-service')
                args = {'path':str(Path(folder)/'out'),'content':'x','bootstrap_id':bid}
                self.assertEqual(self.call('hermes_write_file',args,'chat')['errorCode'],'skill_read_required')
                read = self.call('hermes_skill_read', {'skill_id':'project:deploy-service','bootstrap_id':bid}, 'chat')
                self.assertTrue(read['prerequisites']['execution_ready'])
                self.call('hermes_write_file',args,'chat')
                self.assertEqual(self.native.call_count,1)


if __name__ == '__main__':
    unittest.main()
