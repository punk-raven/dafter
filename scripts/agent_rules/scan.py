from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path

from agent_rules.checks.comments import RULE as COMMENT_RULE
from agent_rules.checks.comments import comment_violations
from agent_rules.checks.em_dash import RULE as EM_DASH_RULE
from agent_rules.checks.em_dash import em_dash_violations
from agent_rules.checks.line_cap import RULE as LINE_CAP_RULE
from agent_rules.checks.line_cap import line_cap_violations
from agent_rules.checks.rule_files import RULE as RULE_FILE_RULE
from agent_rules.checks.rule_files import is_rule_file, rule_file_violations
from agent_rules.exemptions import Exemptions
from agent_rules.files import read_text
from agent_rules.violation import Violation


def file_violations(
    path: str, text: str, repository_files: Sequence[str], exemptions: Exemptions
) -> list[Violation]:
    violations: list[Violation] = []
    if not exemptions.exempts(EM_DASH_RULE, path):
        violations += em_dash_violations(path, text)
    if not exemptions.exempts(LINE_CAP_RULE, path):
        violations += line_cap_violations(path, text)
    if not exemptions.exempts(COMMENT_RULE, path):
        violations += comment_violations(path, text)
    if is_rule_file(path) and not exemptions.exempts(RULE_FILE_RULE, path):
        violations += rule_file_violations(path, text, repository_files)
    return violations


def scan_files(
    root: Path, paths: Iterable[str], repository_files: Sequence[str], exemptions: Exemptions
) -> list[Violation]:
    violations: list[Violation] = []
    for path in paths:
        text = read_text(root / path)
        if text is not None:
            violations += file_violations(path, text, repository_files, exemptions)
    return violations
