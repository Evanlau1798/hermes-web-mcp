import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import runtime  # Sets the installed Hermes dependency environment and import path.


class SkillAdapterTests(unittest.TestCase):
    def test_missing_native_approval_module_never_dispatches(self):
        from skill_adapter import skill_manage_adapter
        from unittest.mock import Mock
        dispatch = Mock(return_value='{}')
        with patch('importlib.import_module', side_effect=ImportError('fixture')):
            result = skill_manage_adapter({'operations': [{'action': 'create', 'name': 'adapter-sample',
                                                          'content': self.content()}]}, native_dispatch=dispatch)
        self.assertEqual(result.get('errorCode'), 'approval_unavailable')
        dispatch.assert_not_called()

    def setUp(self):
        from model_tools import handle_function_call
        from tools import skill_manager_tool, write_approval
        from tools.write_approval import GateDecision

        self.temp = tempfile.TemporaryDirectory(dir=runtime.ROOT / 'tmp')
        self.home = Path(self.temp.name) / "hermes"
        self.root = self.home / "skills"
        self.root.mkdir(parents=True)
        self.manager = skill_manager_tool
        self.handle_function_call = handle_function_call
        self.GateDecision = GateDecision
        self.calls = []
        self.patches = [
            patch.object(self.manager, "SKILLS_DIR", self.root),
            patch.object(self.manager, "_SKILLS_DIR_AT_IMPORT", self.root),
            patch.object(self.manager, "get_hermes_home", return_value=self.home),
            patch("hermes_constants.get_hermes_home", return_value=self.home),
            patch("agent.skill_utils.get_all_skills_dirs", return_value=[self.root]),
            patch("agent.skill_utils.get_skill_create_dir", return_value=None),
            patch.object(write_approval, "evaluate_gate", return_value=GateDecision(allow=True)),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.addCleanup(self.temp.cleanup)

    def dispatch(self, name, arguments, **context):
        self.calls.append((name, arguments, context))
        return self.handle_function_call(
            name, arguments, task_id="adapter-test", session_id="adapter-test",
            tool_call_id="adapter-test-call", enabled_tools=["skill_manage"])

    def invoke(self, operations, dispatch=None):
        from skill_adapter import skill_manage_adapter
        return skill_manage_adapter({"operations": operations}, native_dispatch=dispatch or self.dispatch)

    @staticmethod
    def content(body="Initial guidance."):
        return f"---\nname: adapter-sample\ndescription: Use for adapter tests.\n---\n\n{body}\n"

    def test_create_patch_and_supporting_files_use_native_manager(self):
        created = self.invoke([{"action": "create", "name": "adapter-sample",
                                "content": self.content()}])
        self.assertTrue(created["success"], created)
        patched = self.invoke([{"action": "patch", "name": "adapter-sample",
                                "old_string": "Initial guidance.", "new_string": "Updated guidance."}])
        self.assertTrue(patched["success"], patched)
        wrote = self.invoke([{"action": "write_file", "name": "adapter-sample",
                              "file_path": "references/guide.md", "file_content": "Reference."}])
        self.assertTrue(wrote["success"], wrote)
        removed = self.invoke([{"action": "remove_file", "name": "adapter-sample",
                                "file_path": "references/guide.md"}])
        self.assertTrue(removed["success"], removed)
        self.assertEqual((self.root / "adapter-sample" / "SKILL.md").read_text(encoding="utf-8").splitlines()[-1],
                         "Updated guidance.")
        self.assertFalse((self.root / "adapter-sample" / "references" / "guide.md").exists())
        self.assertTrue(all(call[0] == "skill_manage" for call in self.calls))

    def test_native_batch_is_atomic_and_keeps_lint_metadata(self):
        result = self.invoke([
            {"action": "create", "name": "adapter-sample", "content": self.content()},
            {"action": "patch", "name": "adapter-sample", "old_string": "Initial guidance.",
             "new_string": "Updated guidance."},
            {"action": "write_file", "name": "adapter-sample", "file_path": "references/guide.md",
             "file_content": "Reference."},
        ])
        self.assertTrue(result["success"], result)
        self.assertTrue((self.root / "adapter-sample" / "references" / "guide.md").is_file())

        failed = self.invoke([
            {"action": "create", "name": "rolled-back", "content": self.content()},
            {"action": "patch", "name": "rolled-back", "old_string": "missing",
             "new_string": "replacement"},
        ])
        self.assertFalse(failed["success"])
        self.assertFalse((self.root / "rolled-back").exists())

        warnings = [{"severity": "warning", "rule": "sample", "message": "advisory"}]
        returned = self.invoke([{"action": "patch", "name": "adapter-sample",
                                 "old_string": "x", "new_string": "y"}],
                               dispatch=lambda *_args, **_kwargs: json.dumps(
            {"success": True, "lint_warnings": warnings}))
        self.assertEqual(returned.get("lint_warnings"), warnings)

    def test_native_validation_errors_are_not_reported_as_success(self):
        result = self.invoke([{"action": "create", "name": "adapter-sample", "content": "bad frontmatter"}])
        self.assertFalse(result["success"])
        self.assertIn("frontmatter", result["error"].lower())
        self.assertFalse((self.root / "adapter-sample").exists())

    def test_approval_stage_is_returned_as_required_and_never_replayed(self):
        from tools import write_approval
        write_approval.evaluate_gate = lambda *_args, **_kwargs: self.GateDecision(
            stage=True, message="Staged for approval.")
        with patch.object(write_approval, "stage_write", return_value={"id": "pending-123"}):
            result = self.invoke([{"action": "create", "name": "adapter-sample",
                                   "content": self.content()}])
        self.assertFalse(result["success"])
        self.assertEqual(result["errorCode"], "approval_required")
        self.assertEqual(result["pending_id"], "pending-123")
        self.assertFalse((self.root / "adapter-sample").exists())

    def test_schema_and_support_file_paths_fail_before_native_dispatch(self):
        from skill_adapter import skill_manage_adapter
        result = skill_manage_adapter({"operations": [{"action": "surprise", "name": "x"}]},
                                      native_dispatch=self.dispatch)
        self.assertFalse(result["success"])
        malformed = skill_manage_adapter({"operations": [{"action": [], "name": "x"}]},
                                         native_dispatch=self.dispatch)
        self.assertFalse(malformed["success"])
        self.assertEqual(self.calls, [])
        traversal = self.invoke([{"action": "write_file", "name": "adapter-sample",
                                  "file_path": "references/../../outside.md", "file_content": "x"}])
        self.assertFalse(traversal["success"])
        self.assertEqual(self.calls, [])

    def test_external_create_dir_and_non_hermes_skill_are_rejected(self):
        import agent.skill_utils as skill_utils
        outside = Path(self.temp.name) / "outside"
        with patch.object(skill_utils, "get_skill_create_dir", return_value=outside):
            escaped = self.invoke([{"action": "create", "name": "outside-skill", "content": self.content()}])
        self.assertFalse(escaped["success"])
        self.assertEqual(self.calls, [])

        outside_skill = outside / "project-skill"
        outside_skill.mkdir(parents=True)
        (outside_skill / "SKILL.md").write_text(self.content(), encoding="utf-8")
        with patch.object(self.manager, "_find_skill", return_value={"path": outside_skill}):
            blocked = self.invoke([{"action": "patch", "name": "project-skill",
                                    "old_string": "Initial guidance.", "new_string": "changed"}])
        self.assertFalse(blocked["success"])
        self.assertEqual(self.calls, [])

    def test_symlink_redirects_are_rejected_at_skill_and_file_paths(self):
        (self.root / "redirected").mkdir()
        (self.root / "redirected" / "SKILL.md").write_text(self.content(), encoding="utf-8")
        skill_dir = self.root / "redirected"
        with patch.object(Path, "is_symlink", autospec=True,
                          side_effect=lambda path: path == skill_dir):
            blocked = self.invoke([{"action": "patch", "name": "redirected",
                                    "old_string": "Initial guidance.", "new_string": "changed"}])
        self.assertFalse(blocked["success"])
        self.assertEqual(self.calls, [])

        (self.root / "file-link").mkdir()
        (self.root / "file-link" / "SKILL.md").write_text(self.content(), encoding="utf-8")
        skill_md = self.root / "file-link" / "SKILL.md"
        with patch.object(Path, "is_symlink", autospec=True,
                          side_effect=lambda path: path == skill_md):
            blocked = self.invoke([{"action": "patch", "name": "file-link",
                                    "old_string": "Initial guidance.", "new_string": "changed"}])
        self.assertFalse(blocked["success"])
        self.assertEqual(self.calls, [])
        self.assertIn("Initial guidance.", skill_md.read_text(encoding="utf-8"))

    def test_junction_redirect_is_rejected(self):
        from skill_adapter import skill_manage_adapter
        outside = Path(self.temp.name) / "junction-target"
        outside.mkdir()
        link = self.root / "junction"
        link.mkdir()
        dispatch = lambda *_args, **_kwargs: self.fail("redirected target reached native dispatch")
        with patch.object(Path, "is_junction", autospec=True,
                          side_effect=lambda path: path == link):
            result = skill_manage_adapter({"operations": [{"action": "patch", "name": "junction",
                                                             "old_string": "x", "new_string": "y"}]},
                                          native_dispatch=dispatch)
        self.assertFalse(result["success"])


if __name__ == "__main__":
    unittest.main()
