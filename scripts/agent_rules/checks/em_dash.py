from __future__ import annotations

from agent_rules.violation import Violation

EM_DASH = chr(0x2014)
RULE = "em-dash"


def em_dash_violations(path: str, text: str, first_line: int = 1) -> list[Violation]:
    return [
        Violation(path, first_line + offset, RULE, 'use "-" instead of the em dash')
        for offset, line in enumerate(text.splitlines())
        if EM_DASH in line
    ]
