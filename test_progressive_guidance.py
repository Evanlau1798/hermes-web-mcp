import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import discovery
import runtime
from backend import Bridge, INSTRUCTIONS


class ProgressiveGuidanceTests(unittest.TestCase):
    def test_context_indexes_details_without_eager_domain_workflows(self):
        bridge = Bridge(native=lambda *a, **kw: self.fail('bootstrap must not execute'))
        self.addCleanup(bridge.close)
        with tempfile.TemporaryDirectory(dir=runtime.ROOT/'tmp') as folder, \
             patch.object(discovery, 'SKILLS_ROOT', runtime.ROOT/'skills'), \
             patch.object(discovery, 'CODEX_ROOTS', []):
            context = bridge.call('hermes_context', {'cwd':folder, 'task':'browser use'})
            for key in ('browser_use', 'codex_delivery', 'tracking', 'skill_updates'):
                guide = context[key]['guide']
                result = bridge.call('hermes_skill_read', guide)
                self.assertTrue(result['success'], key)
                self.assertTrue(result['eof'], key)
                self.assertTrue(result['content'].strip(), key)
            self.assertLess(len(json.dumps(context, ensure_ascii=False)), 6500)
            self.assertLess(len(INSTRUCTIONS), 1100)
            self.assertLess(len(json.dumps(context['harness'], ensure_ascii=False)), 1000)
            self.assertNotIn('80000', json.dumps(context, ensure_ascii=False))
            self.assertNotIn('completion_predicate', json.dumps(context, ensure_ascii=False))
            self.assertNotIn('guide', context['skill_updates']['learning'])
            self.assertEqual(len(context['tools']), 27)

    def test_workflow_core_links_readable_on_demand_references(self):
        root = runtime.ROOT/'skills/software-development/hermes-web-mcp-workflows'
        core = (root/'SKILL.md').read_text(encoding='utf-8')
        self.assertLess(len(core), 1800)
        links = re.findall(r'\]\((references/[^)]+)\)', core)
        self.assertTrue({'references/browser.md', 'references/mcp-images.md',
                         'references/codex-send.md', 'references/tracking.md',
                         'references/skill-learning.md'} <= set(links))
        for link in links:
            self.assertTrue((root/link).is_file(), link)
        browser = (root/'references/browser.md').read_text(encoding='utf-8')
        for contract in ('action=guide', '官方指南', '結尾', 'TaskSpace',
                         'waitForURL', 'waitForFunction', 'mayHaveLateEffects',
                         '主實體', 'finish', 'mcp-images.md', 'browserScreenshotPath'):
            self.assertIn(contract, browser)


if __name__ == '__main__':
    unittest.main()
