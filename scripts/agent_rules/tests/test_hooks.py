import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_rules.checks.em_dash import EM_DASH
from agent_rules.hooks import BLOCKING_EXIT_CODE, post_edit, pre_edit, stop


def git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)


class HookTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name).resolve()
        git(self.root, "init", "-q")
        (self.root / "main.go").write_text("package main\n")
        (self.root / ".gitignore").write_text("ignored/\n")
        git(self.root, "add", ".")
        git(self.root, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "init")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def write_payload(self, relative: str, content: str) -> dict[str, object]:
        file_path = str(self.root / relative)
        return {"tool_name": "Write", "tool_input": {"file_path": file_path, "content": content}}

    def test_pre_edit_blocks_an_em_dash(self) -> None:
        outcome = pre_edit(self.root, self.write_payload("notes.md", f"a {EM_DASH} b\n"))
        self.assertEqual(outcome.exit_code, BLOCKING_EXIT_CODE)
        self.assertIn("em-dash", outcome.stderr)

    def test_pre_edit_blocks_a_file_over_the_cap(self) -> None:
        outcome = pre_edit(self.root, self.write_payload("big.go", "x\n" * 501))
        self.assertEqual(outcome.exit_code, BLOCKING_EXIT_CODE)

    def test_pre_edit_blocks_a_rule_file_without_paths(self) -> None:
        outcome = pre_edit(self.root, self.write_payload(".agents/rules/a.md", "# A\n"))
        self.assertIn("rule-file", outcome.stderr)

    def test_pre_edit_checks_only_the_new_text_of_an_edit(self) -> None:
        (self.root / "old.md").write_text(f"legacy {EM_DASH}\nkeep\n")
        tool_input = {
            "file_path": str(self.root / "old.md"),
            "old_string": "keep",
            "new_string": "kept",
        }
        outcome = pre_edit(self.root, {"tool_name": "Edit", "tool_input": tool_input})
        self.assertEqual(outcome.exit_code, 0)

    def test_pre_edit_ignores_paths_outside_the_repository_and_ignored_paths(self) -> None:
        outside = {
            "tool_name": "Write",
            "tool_input": {"file_path": "/tmp/x.md", "content": EM_DASH},
        }
        self.assertEqual(pre_edit(self.root, outside).exit_code, 0)
        ignored = self.write_payload("ignored/x.md", EM_DASH)
        self.assertEqual(pre_edit(self.root, ignored).exit_code, 0)

    def test_post_edit_reports_only_introduced_comments(self) -> None:
        (self.root / "main.go").write_text("package main\n\n// old\nvar a = 1 // added\n")
        tool_input = {
            "file_path": str(self.root / "main.go"),
            "old_string": "var a = 1",
            "new_string": "var a = 1 // added",
        }
        outcome = post_edit(self.root, {"tool_name": "Edit", "tool_input": tool_input})
        reason = json.loads(outcome.stdout)["reason"]
        self.assertIn("main.go:4", reason)
        self.assertNotIn("main.go:3", reason)

    def test_stop_blocks_on_changed_files_unless_already_continuing(self) -> None:
        (self.root / "main.go").write_text("package main\n\n// note\n")
        self.assertEqual(json.loads(stop(self.root, {}).stdout)["decision"], "block")
        self.assertEqual(stop(self.root, {"stop_hook_active": True}).stdout, "")

    def test_stop_passes_a_clean_tree(self) -> None:
        self.assertEqual(stop(self.root, {}).stdout, "")


if __name__ == "__main__":
    unittest.main()
