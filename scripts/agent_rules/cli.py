from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agent_rules.exemptions import load_exemptions
from agent_rules.files import repository_files, repository_root
from agent_rules.hooks import HookOutcome, post_edit, pre_edit, stop
from agent_rules.scan import scan_files

USAGE = "usage: python3 -m agent_rules {check|pre-edit|post-edit|stop}"
HOOKS: dict[str, Callable[[Path, dict[str, Any]], HookOutcome]] = {
    "pre-edit": pre_edit,
    "post-edit": post_edit,
    "stop": stop,
}


def check(root: Path) -> int:
    files = repository_files(root)
    violations = scan_files(root, files, files, load_exemptions(root))
    for violation in violations:
        print(violation.render())
    if violations:
        print(f"{len(violations)} rule violations", file=sys.stderr)
        return 1
    return 0


def run_hook(root: Path, mode: str) -> int:
    raw_input = sys.stdin.read()
    payload = json.loads(raw_input) if raw_input.strip() else {}
    outcome = HOOKS[mode](root, payload)
    if outcome.stdout:
        print(outcome.stdout)
    if outcome.stderr:
        print(outcome.stderr, file=sys.stderr)
    return outcome.exit_code


def main(arguments: list[str]) -> int:
    if len(arguments) != 1 or arguments[0] not in ("check", *HOOKS):
        print(USAGE, file=sys.stderr)
        return 2
    root = repository_root()
    if arguments[0] == "check":
        return check(root)
    return run_hook(root, arguments[0])
