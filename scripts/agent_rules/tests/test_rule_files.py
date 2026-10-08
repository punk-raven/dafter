import unittest

from agent_rules.checks.rule_files import parse_frontmatter, rule_file_violations
from agent_rules.globs import expand_braces, matches_glob

FILES = ["go/internal/control/call.go", "go/internal/control/held.go", "python/a/telephony.py"]


class GlobTest(unittest.TestCase):
    def test_expands_nested_braces(self) -> None:
        self.assertEqual(expand_braces("{a,b}/{c,d}"), ["a/c", "a/d", "b/c", "b/d"])

    def test_double_star_spans_directories(self) -> None:
        self.assertTrue(matches_glob("go/internal/control/call.go", "go/**/*.go"))
        self.assertTrue(matches_glob("call.go", "**/call.go"))
        self.assertFalse(matches_glob("go/internal/call.go", "go/*.go"))


class RuleFileTest(unittest.TestCase):
    def test_accepts_a_quoted_list(self) -> None:
        text = '---\npaths:\n  - "go/internal/control/{call,held}.go"\n---\n# Telephony\n'
        self.assertEqual(rule_file_violations(".agents/rules/t.md", text, FILES), [])

    def test_accepts_a_quoted_comma_string(self) -> None:
        frontmatter = parse_frontmatter('---\npaths: "a/*.go, b/**"\n---\n')
        self.assertEqual(frontmatter.globs, ["a/*.go", "b/**"])
        self.assertEqual(frontmatter.problems, [])

    def test_reports_missing_frontmatter(self) -> None:
        self.assertTrue(rule_file_violations(".agents/rules/t.md", "# Telephony\n", FILES))

    def test_reports_unquoted_globs_and_unknown_keys(self) -> None:
        text = "---\npaths:\n  - go/**\nname: x\n---\n"
        messages = [v.message for v in rule_file_violations(".agents/rules/t.md", text, FILES)]
        self.assertIn("quote every glob in paths", messages)
        self.assertIn("unexpected frontmatter line: name: x", messages)

    def test_reports_each_brace_alternative_that_matches_nothing(self) -> None:
        text = '---\npaths:\n  - "go/internal/control/{call,gone}.go"\n---\n'
        messages = [v.message for v in rule_file_violations(".agents/rules/t.md", text, FILES)]
        self.assertEqual(messages, ["glob matches no file: go/internal/control/gone.go"])


if __name__ == "__main__":
    unittest.main()
