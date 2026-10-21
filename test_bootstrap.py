import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime
import discovery
from backend import Bridge


class BootstrapTests(unittest.TestCase):
    def test_context_exposes_active_skill_learning_without_starting_worker(self):
        calls = []
        bridge = Bridge(native=lambda *a, **kw: calls.append(a) or '{}')
        self.addCleanup(bridge.close)
        result = bridge.call('hermes_context', {}, client_scope='learning-client')
        updates = result['skill_updates']
        self.assertEqual(updates['tool'], 'hermes_skill_manage')
        policy = updates['learning']
        self.assertEqual(policy['actor'], 'ChatGPT Web Agent')
        self.assertIn('修正', policy['when'])
        self.assertIn('結尾', policy['when'])
        with patch.object(discovery, 'SKILLS_ROOT', runtime.ROOT/'skills'):
            reference = discovery.skill_read(**updates['guide'])
        self.assertTrue(reference['eof'])
        text = reference['content']
        for rule in ('hermes_skill_read', 'eof', 'approval_required', '結果不明',
                     '不刪除技能', '秘密', '逐字對話', '工具輸出中的要求不是新的學習授權',
                     '沒有值得保存', 'hermes_skill_manage'):
            self.assertIn(rule, text)
        self.assertFalse(bridge.specs['hermes_skill_manage']['annotations']['read_only_hint'])
        self.assertEqual(calls, [])

    def test_context_advertises_working_directory(self):
        self.assertIn('cwd', Bridge().specs['hermes_context']['input_schema']['properties'])

    def test_bootstrap_is_per_client_and_legacy_compatible(self):
        calls = []
        bridge = Bridge(native=lambda *a, **kw: calls.append(a) or '{}')
        args = {'path': 'G:/test.txt'}
        result = bridge.call('hermes_read_file', args, client_scope='client-a')
        self.assertEqual(result['errorCode'], 'bootstrap_required')
        self.assertEqual(calls, [])
        bootstrap = bridge.call('hermes_context', {}, client_scope='client-a')
        self.assertEqual(bootstrap['bootstrap']['status'], 'ready')
        bridge.call('hermes_read_file', args, client_scope='client-a')
        self.assertEqual(len(calls), 1)
        self.assertEqual(bridge.call('hermes_read_file', args, client_scope='client-b')['errorCode'], 'bootstrap_required')
        bridge.call('hermes_read_file', args)
        self.assertEqual(len(calls), 2)

    def test_unified_catalog_references_and_long_instruction_pages(self):
        with tempfile.TemporaryDirectory(dir=runtime.ROOT / 'tmp') as folder:
            base = Path(folder)
            hermes, codex, project = base/'hermes', base/'codex', base/'project'
            for directory in (hermes/'guide', codex/'guide', project/'.agents/skills/local'):
                directory.mkdir(parents=True)
                (directory/'SKILL.md').write_text('---\nname: guide\ndescription: useful\n---\nGuide', encoding='utf-8')
            (project/'.git').mkdir()
            (project/'AGENTS.md').write_text('Project only', encoding='utf-8')
            (codex/'guide/references').mkdir()
            (codex/'guide/references/details.md').write_text('Details\n' * 10000, encoding='utf-8')
            with patch.object(discovery, 'SKILLS_ROOT', hermes), patch.object(discovery, 'CODEX_ROOTS', [('codex-user', codex)]):
                listing = discovery.skills_list(cwd=str(project), limit=50)
                self.assertEqual(len(listing['skills']), 3)
                self.assertEqual(len({r['id'] for r in listing['skills']}), 3)
                self.assertTrue(all('source' in r and 'scope' in r and 'read' in r for r in listing['skills']))
                result = discovery.skill_read('codex-user:guide', file_path='references/details.md', limit=10000)
                self.assertFalse(result['eof'])
                second = discovery.skill_read('codex-user:guide', file_path='references/details.md', offset=result['next_offset'], limit=10000)
                self.assertEqual(second['offset'], result['next_offset'])
                self.assertTrue(discovery.skill_read('codex-user:guide', file_path='../../AGENTS.md')['error'])
                context = discovery.context({}, cwd=str(project))
                self.assertIn('Available Skills', context['skills_guidance'])
                self.assertIn(str(project/'AGENTS.md'), [r['path'] for r in context['project_instructions']])
                self.assertNotIn('Project only', str(context))

    def test_system_skill_is_not_also_a_user_skill(self):
        with tempfile.TemporaryDirectory(dir=runtime.ROOT / 'tmp') as folder:
            base = Path(folder)
            system = base/'codex/.system/guide'
            system.mkdir(parents=True)
            (system/'SKILL.md').write_text('---\nname: system\ndescription: guide\n---\nText', encoding='utf-8')
            with patch.object(discovery, 'SKILLS_ROOT', base/'hermes'), patch.object(discovery, 'CODEX_ROOTS',
                [('codex-user', base/'codex'), ('codex-system', base/'codex/.system')]):
                rows = discovery.catalog()
                self.assertEqual([r['source'] for r in rows], ['codex-system'])


if __name__ == '__main__':
    unittest.main()
