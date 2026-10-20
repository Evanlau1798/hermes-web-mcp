import json
import tempfile
import subprocess
import sys
import unittest
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import runtime
from backend import Bridge


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.bridge = Bridge(native=lambda name, args, **kw: self.calls.append((name, args, kw)) or '{}')

    def test_inventory_and_native_schema_validation(self):
        expected = {'hermes_read_file', 'hermes_search_files', 'hermes_write_file', 'hermes_patch',
                    'hermes_terminal', 'hermes_process_manage', 'hermes_process_read', 'hermes_memory_read', 'hermes_memory_edit',
                    'codex_list', 'codex_status', 'codex_read', 'codex_send', 'codex_resume', 'codex_stop',
                    'hermes_context', 'hermes_skills_list', 'hermes_skill_read', 'hermes_computer_use',
                    'hermes_skill_manage', 'hermes_tracking', 'hermes_tracking_status', 'codex_delivery_status',
                    'codex_prepare_send', 'codex_confirm_send', 'codex_operation_status'}
        self.assertEqual(set(self.bridge.specs), expected)
        self.bridge.call('hermes_read_file', {'path': 'G:/test.txt'})
        name, args, context = self.calls[-1]
        self.assertEqual(name, 'read_file')
        self.assertTrue(context['task_id'].startswith('hermes-web-'))
        with self.assertRaises(ValueError):
            self.bridge.call('hermes_terminal', {'command': 'echo ok', 'force': True})
        with self.assertRaises(ValueError):
            self.bridge.call('hermes_memory_read', {'target': '../../private'})
        self.assertEqual(len(self.calls), 1)

    def test_context_and_passive_skill_discovery(self):
        import discovery
        with tempfile.TemporaryDirectory(dir=runtime.ROOT / 'tmp') as directory:
            root = Path(directory) / 'skills'
            skill = root / 'dev' / 'windows'
            skill.mkdir(parents=True)
            content = '---\nname: Windows guide\ndescription: 繁體 CLI\ndeps: [must-not-install]\n---\n!`must-not-execute`\n'
            (skill / 'SKILL.md').write_text(content, encoding='utf-8', newline='')
            outside = Path(directory) / 'SKILL.md'
            outside.write_text('PRIVATE CONTENT', encoding='utf-8')
            with patch.object(discovery, 'SKILLS_ROOT', root), patch.object(discovery, 'CODEX_ROOTS', []):
                overview = self.bridge.call('hermes_context', {})
                self.assertEqual(set(overview['tools']), set(self.bridge.specs))
                self.assertFalse(overview['execution']['model_started'])
                listing = self.bridge.call('hermes_skills_list', {'limit': 1})
                self.assertEqual(listing['skills'][0]['id'], 'dev/windows')
                self.assertNotIn('content', listing['skills'][0])
                self.assertEqual(self.bridge.call('hermes_skill_read', {'skill_id': 'dev/windows'})['content'], content)
                self.assertEqual(self.bridge.call('hermes_skills_list', {'query': 'missing'})['total'], 0)
                for name in ('../SKILL.md', str(outside), 'dev/windows/../../..', 'dev/windows/SKILL.md'):
                    self.assertFalse(self.bridge.call('hermes_skill_read', {'skill_id': name})['success'])
                with patch.object(discovery, 'iter_skill_index_files', return_value=iter([outside])):
                    self.assertEqual(self.bridge.call('hermes_skills_list', {})['total'], 0)
                for attribute in ('is_symlink', 'is_junction'):
                    with patch.object(Path, attribute, return_value=True):
                        with self.assertRaises(ValueError):
                            self.bridge.call('hermes_skills_list', {})
                        with self.assertRaises(ValueError):
                            self.bridge.call('hermes_skill_read', {'skill_id': 'dev/windows'})
                (skill / 'SKILL.md').write_text('x' * 48001, encoding='utf-8')
                self.assertFalse(self.bridge.call('hermes_skill_read', {'skill_id': 'dev/windows'})['success'])
                with self.assertRaises(ValueError):
                    self.bridge.call('hermes_skills_list', {'limit': 51})
        self.assertEqual(self.calls, [])

    def test_foreign_process_and_handoff_rejected_before_dispatch(self):
        with self.assertRaises(ValueError):
            self.bridge.call('hermes_process_manage', {'action': 'kill', 'session_id': 'foreign'})
        with self.assertRaises(ValueError):
            self.bridge.call('hermes_process_manage', {'action': 'handoff', 'session_id': 'foreign'})
        self.assertEqual(self.calls, [])
        with patch('backend.process_registry.get', return_value=SimpleNamespace(task_id='container-key', owner_task_id=self.bridge.owner)):
            self.bridge.call('hermes_process_manage', {'action': 'poll', 'session_id': 'owned'})
        self.assertEqual(self.calls[-1][0], 'process_manage')

    def test_memory_uses_native_atomic_store(self):
        from tools.memory_tool_store import MemoryStore
        import tools.memory_tool as mt
        with tempfile.TemporaryDirectory(dir=runtime.ROOT / 'tmp') as directory:
            location = Path(directory) / 'memories'
            def fresh():
                store = MemoryStore(160, 100)
                store.load_from_disk()
                return store
            with patch.object(mt, 'get_memory_dir', return_value=location), patch.object(mt, 'load_on_disk_store', side_effect=fresh):
                def edit(**kw):
                    return self.bridge.call('hermes_memory_edit', {'target': 'memory', **kw})
                self.assertTrue(edit(action='add', content='keep unrelated')['success'])
                self.assertTrue(edit(action='add', content='繁體中文')['success'])
                self.assertTrue(edit(action='add', content='繁體中文')['success'])
                self.assertTrue(edit(action='patch', old_text='繁體中文', content='繁體中文與 English')['success'])
                self.assertFalse(edit(action='add', content='x' * 200)['success'])
                self.assertFalse(edit(action='patch', old_text='missing', content='no')['success'])
                self.assertTrue(edit(operations=[{'action': 'remove', 'old_text': '繁體中文與 English'},
                                                 {'action': 'add', 'content': 'replacement'}])['success'])
                result = self.bridge.call('hermes_memory_read', {'target': 'memory'})
                self.assertEqual(result['entries'], ['keep unrelated', 'replacement'])
                self.assertEqual(result['limit'], 160)
                second = Bridge()
                with ThreadPoolExecutor(max_workers=2) as pool:
                    results = list(pool.map(lambda pair: pair[0].call('hermes_memory_edit',
                        {'target': 'memory', 'action': 'add', 'content': pair[1]}),
                        [(self.bridge, 'concurrent A'), (second, 'concurrent B')]))
                self.assertTrue(all(r['success'] for r in results))
                entries = self.bridge.call('hermes_memory_read', {'target': 'memory'})['entries']
                self.assertEqual(len(entries), 4)
                self.assertIn('keep unrelated', entries)
                self.assertTrue(edit(action='add', content='same same')['success'])
                self.assertFalse(edit(action='patch', old_text='same', content='ambiguous')['success'])

    def test_native_direct_execution_preserves_hard_guards(self):
        import tools.terminal_tool as terminal
        marker = runtime.ROOT / 'tmp' / 'must-not-run.txt'
        self.assertFalse(marker.exists())
        with patch.object(terminal, '_check_all_guards', return_value={'approved': False, 'message': 'approval unavailable'}):
            result = Bridge().call('hermes_terminal', {'command': 'echo forbidden > must-not-run.txt',
                                                      'workdir': str(marker.parent), 'timeout': 5})
        self.assertFalse(marker.exists())
        self.assertTrue(result.get('error'))
        decisions = []
        def consent(*args, **kw):
            decisions.append(args)
            return 'deny'
        with patch.object(terminal, '_run_foreground', return_value='{}') as execute:
            Bridge().call('hermes_terminal', {'command': 'rm -rf ./mcp-test-nonexistent',
                                            'workdir': str(marker.parent), 'timeout': 5}, approve=consent)
            execute.assert_called_once()
        self.assertEqual(decisions, [])
        from tools import approval
        with patch.object(approval, '_match_user_deny_rule', return_value='explicit-deny'), \
                patch.object(terminal, '_run_foreground', return_value='{}') as execute:
            result = Bridge().call('hermes_terminal', {'command': 'echo blocked',
                                    'workdir': str(marker.parent), 'timeout': 5})
            execute.assert_not_called()
            self.assertTrue(result.get('error'))
        with patch.object(terminal, '_run_foreground', return_value='{}') as execute:
            result = Bridge().call('hermes_terminal', {'command': 'rm -rf /',
                                    'workdir': str(marker.parent), 'timeout': 5})
            execute.assert_not_called()
            self.assertTrue(result.get('error'))

    def test_codex_literal_input_and_unknown_outcome_not_retried(self):
        calls = []
        def codex(action, args):
            calls.append((action, args))
            return {'deliveryState': 'unknown', 'automaticRetrySafe': False}
        bridge = Bridge(codex=codex)
        target = {'thread_id': '11111111-1111-4111-8111-111111111111', 'expected_title': 'Synthetic fixture conversation'}
        for action in ('send', 'resume', 'stop'):
            with self.assertRaises(ValueError):
                bridge.call('codex_' + action, {'thread_id': target['thread_id'], **({'message': 'test'} if action == 'send' else {})})
        self.assertEqual(calls, [])
        result = bridge.call('codex_send', {**target, 'operation_id': 'literal-test-1', 'message': '$() `x`\n繁體'})
        self.assertEqual(calls[0][1]['message'], '$() `x`\n繁體')
        self.assertEqual(len(calls), 1)
        self.assertFalse(result['automaticRetrySafe'])

    def test_fresh_native_startup_never_reads_persona_or_connects_network(self):
        code = '''import sys, os
attempts=[]
def audit(event,args):
 if event=='open' and isinstance(args[0],(str,bytes)):
  name=os.fsdecode(args[0]).lower()
  if name.endswith('soul.md') or '.private.' in name:
   attempts.append('persona');raise RuntimeError('persona forbidden')
 if event=='socket.connect':
  attempts.append('network');raise RuntimeError('network forbidden')
sys.addaudithook(audit)
from pathlib import Path
from unittest.mock import patch
from backend import Bridge
from tools.memory_tool_store import MemoryStore
b=Bridge();b.call('hermes_read_file',{'path':str(Path.cwd()/'.gitignore')})
b.call('hermes_context',{})
skills=b.call('hermes_skills_list',{'limit':1})
b.call('hermes_skill_read',{'skill_id':skills['skills'][0]['id']})
with patch('tools.memory_tool.load_on_disk_store',return_value=MemoryStore(160,100)):
 b.call('hermes_memory_read',{'target':'memory'})
b.close()
assert not attempts, attempts
'''
        result = subprocess.run([sys.executable, '-c', code], cwd=runtime.ROOT, capture_output=True,
                                text=True, encoding='utf-8', timeout=40)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    (runtime.ROOT / 'tmp').mkdir(exist_ok=True)
    unittest.main()
