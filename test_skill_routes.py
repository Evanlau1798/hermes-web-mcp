import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime
import discovery


class SkillRouteTests(unittest.TestCase):
    def test_codex_home_is_resolved_on_local_endpoint_at_call_time(self):
        with tempfile.TemporaryDirectory(dir=runtime.ROOT / 'tmp') as folder:
            base = Path(folder)
            for name in ('first', 'second'):
                skill = base / name / 'skills' / name
                skill.mkdir(parents=True)
                (skill / 'SKILL.md').write_text(
                    f'---\nname: {name}\ndescription: test\n---\nGuide', encoding='utf-8')
            with patch.object(discovery, 'SKILLS_ROOT', base / 'hermes'):
                for name in ('first', 'second'):
                    with patch.dict(os.environ, {'CODEX_HOME': str(base / name)}):
                        rows = discovery.catalog()
                        row = next(r for r in rows if r['id'] == 'codex-user:' + name)
                        self.assertEqual(Path(row['locator']).parent.parent, base / name / 'skills')
                for invalid in ('relative-home', ''):
                    with patch.dict(os.environ, {'CODEX_HOME': invalid}):
                        with self.assertRaises(ValueError):
                            discovery.skill_roots()

    def test_routes_distinguish_authorship_from_local_installation(self):
        with tempfile.TemporaryDirectory(dir=runtime.ROOT / 'tmp') as folder:
            base = Path(folder)
            project = base / 'project'
            project.mkdir()
            roots = [('hermes', base / 'hermes'), ('codex-user', base / 'codex'),
                     ('codex-system', base / 'system'), ('codex-agents', base / 'agents'),
                     ('project', project / '.agents/skills')]
            for source, root in roots:
                skill = root / 'sample'
                skill.mkdir(parents=True)
                (skill / 'SKILL.md').write_text(
                    '---\nname: sample\ndescription: test\n---\nGuide', encoding='utf-8')
            with patch.object(discovery, 'SKILLS_ROOT', roots[0][1]), \
                 patch.object(discovery, 'CODEX_ROOTS', roots[1:4]):
                context = discovery.context({}, cwd=str(project))
                self.assertEqual(context['execution']['environment'], 'local-windows')
                targets = context['skill_updates']['targets']
                self.assertEqual({r['source'] for r in targets}, {r[0] for r in roots})
                routes = {r['source']: r['route'] for r in targets}
                self.assertEqual(routes['hermes'], 'hermes_skill_manage')
                self.assertEqual(routes['codex-system'], 'reference_only')
                for source in ('codex-user', 'codex-agents', 'project'):
                    self.assertEqual(routes[source], 'hermes_files_explicit_request')
                for row in discovery.catalog(str(project)):
                    self.assertEqual(row['maintenance']['environment'], 'local-windows')
                    self.assertEqual(row['maintenance']['route'], routes[row['source']])
                    read = discovery.skill_read(row['id'], cwd=str(project))
                    self.assertEqual(read['maintenance'], row['maintenance'])
                    self.assertTrue(read['eof'])


if __name__ == '__main__':
    unittest.main()
