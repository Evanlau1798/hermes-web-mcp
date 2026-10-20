import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime
import discovery
from backend import Bridge


class WorkflowTests(unittest.TestCase):
    def test_task_ranking_typo_and_compact_compatible_bootstrap(self):
        with tempfile.TemporaryDirectory(dir=runtime.ROOT / 'tmp') as folder:
            root = Path(folder)
            names = {
                'merge-upstream-safely': 'Audit and integrate pinned upstream releases into a divergent fork.',
                'agent-handoff-discipline': 'Development and handoff discipline for repository work.',
                'deploy-service': 'Release and deployment of a service.',
                'merge-branches': 'Merge local git branches.',
            }
            for name, description in names.items():
                skill = root / name
                skill.mkdir()
                (skill / 'SKILL.md').write_text(
                    f'---\nname: {name}\ndescription: {description}\n---\nGuide', encoding='utf-8')
            with patch.object(discovery, 'SKILLS_ROOT', root), patch.object(discovery, 'CODEX_ROOTS', []):
                bridge = Bridge(native=lambda *a, **kw: '{}')
                for query in ('merge-upstream-safety', '準備 merge 上游最新版', '整合上游更新'):
                    result = bridge.call('hermes_skills_list', {'query': query})
                    self.assertEqual(result['skills'][0]['id'], 'merge-upstream-safely')
                    self.assertTrue(result['skills'][0]['match_reason'])
                    self.assertEqual(result['skills'][0]['role'], 'primary')
                for task in ('準備 merge 上游最新版', 'rebase', '部署服務', '覆寫設定'):
                    result = bridge.call('hermes_context', {'task': task}, client_scope='a')
                    self.assertTrue(result['workflow']['risk'])
                result = bridge.call('hermes_context', {'task': '準備 merge 上游最新版'}, client_scope='a')
                self.assertEqual(result['workflow']['required_skill_ids'], ['merge-upstream-safely'])
                self.assertFalse(result.get('truncated'))
                self.assertLess(len(json.dumps(result, ensure_ascii=False)), 10000)
                self.assertEqual(set(result['tools']), set(bridge.specs))
                self.assertEqual(result['available_skills']['skills'][0]['id'], 'merge-upstream-safely')
                blocked = bridge.call('hermes_write_file', {'path': 'G:/unused', 'content': 'x'}, client_scope='a')
                self.assertEqual(blocked['errorCode'], 'skill_read_required')
                # Reading only the final page must not satisfy the prerequisite.
                bridge.call('hermes_skill_read', {'skill_id': 'merge-upstream-safely', 'offset': 20}, client_scope='a')
                self.assertEqual(bridge.call('hermes_write_file', {'path': 'G:/unused', 'content': 'x'}, client_scope='a')['errorCode'], 'skill_read_required')
                first = bridge.call('hermes_skill_read', {'skill_id': 'merge-upstream-safely', 'limit': 20}, client_scope='a')
                bridge.call('hermes_skill_read', {'skill_id': 'merge-upstream-safely', 'offset': first['next_offset']}, client_scope='a')
                self.assertNotIn('errorCode', bridge.call('hermes_write_file', {'path': 'G:/unused', 'content': 'x'}, client_scope='a'))
                ordinary = bridge.call('hermes_context', {'task': '讀取測試檔'}, client_scope='a')
                self.assertFalse(ordinary['workflow']['risk'])
                self.assertNotIn('errorCode', bridge.call('hermes_write_file', {'path': 'G:/unused', 'content': 'x'}, client_scope='a'))
                legacy = bridge.call('hermes_context', {}, client_scope='a')
                self.assertEqual(legacy['bootstrap']['status'], 'ready')
                self.assertEqual(legacy['available_skills']['skills'], [])
                self.assertEqual(legacy['available_skills']['total'], 4)
                chinese = bridge.call('hermes_context', {'task': '合併分支'}, client_scope='zh')
                self.assertEqual(chinese['workflow']['required_skill_ids'], ['merge-branches'])
                self.assertEqual(bridge.call('hermes_write_file', {'path': 'G:/unused', 'content': 'x'}, client_scope='zh')['errorCode'], 'skill_read_required')
                bridge.call('hermes_skill_read', {'skill_id': 'merge-branches'}, client_scope='zh')
                self.assertNotIn('errorCode', bridge.call('hermes_write_file', {'path': 'G:/unused', 'content': 'x'}, client_scope='zh'))
                ordinary_merge = bridge.call('hermes_context', {'task': 'merge CSV rows'}, client_scope='a')
                self.assertFalse(ordinary_merge['workflow']['risk'])

    def test_terminal_receipt_nonzero_timeout_partial_and_guard(self):
        from terminal_adapter import terminal_receipt
        receipt = terminal_receipt({'exit_code': 1, 'output': 'valid output', 'error': None})
        self.assertEqual(receipt['outcome'], 'shell_exit')
        self.assertFalse(receipt['timed_out'])
        self.assertFalse(receipt['automaticRetrySafe'])
        self.assertIsNone(receipt['stderr'])
        timed = terminal_receipt({'exit_code': 124, 'output': '', 'error': 'Command timed out after 1 seconds'})
        self.assertTrue(timed['timed_out'])
        self.assertEqual(timed['outcome'], 'execution_error')
        normal124 = terminal_receipt({'exit_code': 124, 'output': '', 'error': None})
        self.assertFalse(normal124['timed_out'])
        native_timeout = terminal_receipt({'exit_code': 124, 'error': None,
                                           'output': 'partial\n[Command timed out after 1s]'})
        self.assertTrue(native_timeout['timed_out'])
        spilled = terminal_receipt({'exit_code': 0, 'error': None, 'output': 'short',
                                    'output_total_chars': 100000, 'truncation_note': 'Spilled'})
        self.assertTrue(spilled['partial_output'])
        long = terminal_receipt({'exit_code': 1, 'output': 'a' * 80000 + 'tail', 'error': None})
        self.assertTrue(long['partial_output'])
        self.assertTrue(long['output'].endswith('tail'))
        self.assertLess(len(json.dumps(long)), 50000)
        denied = terminal_receipt({'exit_code': -1, 'error': 'deny', 'output': ''})
        self.assertEqual(denied['outcome'], 'execution_error')


if __name__ == '__main__':
    unittest.main()
