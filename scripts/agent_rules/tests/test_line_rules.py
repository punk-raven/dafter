import unittest

from agent_rules.checks.em_dash import EM_DASH, em_dash_violations
from agent_rules.checks.line_cap import line_cap_for, line_cap_violations
from agent_rules.exemptions import Exemptions


class EmDashTest(unittest.TestCase):
    def test_reports_each_line_with_an_em_dash(self) -> None:
        text = f"fine\nbad {EM_DASH} here\nfine\n"
        self.assertEqual([v.line for v in em_dash_violations("a.md", text)], [2])

    def test_offsets_lines_from_the_first_line(self) -> None:
        self.assertEqual([v.line for v in em_dash_violations("a.md", EM_DASH, 10)], [10])


class LineCapTest(unittest.TestCase):
    def test_instruction_files_have_the_smaller_cap(self) -> None:
        self.assertEqual(line_cap_for("AGENTS.md"), 100)
        self.assertEqual(line_cap_for(".agents/rules/config.md"), 100)
        self.assertEqual(line_cap_for(".agents/skills/project-context/rules.md"), 100)
        self.assertEqual(line_cap_for("go/internal/a.go"), 500)

    def test_reports_a_file_over_its_cap(self) -> None:
        self.assertEqual(line_cap_violations("a.go", "x\n" * 500), [])
        self.assertEqual(len(line_cap_violations("a.go", "x\n" * 501)), 1)


class ExemptionsTest(unittest.TestCase):
    def test_rule_specific_and_global_globs(self) -> None:
        exemptions = Exemptions({"line-cap": ["docs/*.md"], "*": [".agents/skills/vendor/**"]})
        self.assertTrue(exemptions.exempts("line-cap", "docs/dafter.md"))
        self.assertFalse(exemptions.exempts("em-dash", "docs/dafter.md"))
        self.assertTrue(exemptions.exempts("em-dash", ".agents/skills/vendor/a/b.md"))


if __name__ == "__main__":
    unittest.main()
