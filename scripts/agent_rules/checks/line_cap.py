from __future__ import annotations

from agent_rules.globs import matches_any_glob
from agent_rules.violation import Violation

RULE = "line-cap"
FILE_LINE_CAP = 500
INSTRUCTION_LINE_CAP = 100
INSTRUCTION_FILE_GLOBS = (
    "AGENTS.md",
    ".agents/rules/**/*.md",
    ".agents/skills/project-context/*.md",
)


def line_cap_for(path: str) -> int:
    if matches_any_glob(path, INSTRUCTION_FILE_GLOBS):
        return INSTRUCTION_LINE_CAP
    return FILE_LINE_CAP


def count_lines(text: str) -> int:
    return len(text.splitlines())


def line_cap_violations(path: str, text: str) -> list[Violation]:
    cap = line_cap_for(path)
    lines = count_lines(text)
    if lines <= cap:
        return []
    return [
        Violation(path, cap + 1, RULE, f"{lines} lines, over the {cap}-line cap; split the file")
    ]
