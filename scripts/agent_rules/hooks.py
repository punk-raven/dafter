from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_rules.checks.comments import RULE as COMMENT_RULE
from agent_rules.checks.comments import comment_violation, comments_in, is_generated
from agent_rules.checks.em_dash import RULE as EM_DASH_RULE
from agent_rules.checks.em_dash import em_dash_violations
from agent_rules.checks.line_cap import RULE as LINE_CAP_RULE
from agent_rules.checks.line_cap import line_cap_violations
from agent_rules.checks.rule_files import is_rule_file, rule_file_violations
from agent_rules.exemptions import Exemptions, load_exemptions
from agent_rules.files import (
    changed_files,
    is_ignored,
    read_text,
    relative_to_root,
    repository_files,
)
from agent_rules.globs import matches_glob
from agent_rules.scan import scan_files
from agent_rules.violation import Violation

BLOCKING_EXIT_CODE = 2
STOP_REPORT_LIMIT = 30
RULE_FILE_DIRECTORY_GLOB = ".agents/rules/**/*.md"


@dataclass(frozen=True)
class HookOutcome:
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""


def block_decision(reason: str) -> HookOutcome:
    return HookOutcome(stdout=json.dumps({"decision": "block", "reason": reason}))


def render_report(violations: list[Violation], limit: int = STOP_REPORT_LIMIT) -> str:
    lines = [violation.render() for violation in violations[:limit]]
    if len(violations) > limit:
        lines.append(f"... and {len(violations) - limit} more; run `make rules-check`")
    return "\n".join(lines)


def edited_path(root: Path, tool_input: dict[str, Any]) -> str | None:
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
    if not isinstance(file_path, str):
        return None
    relative = relative_to_root(root, file_path)
    if relative is None or is_ignored(root, relative):
        return None
    return relative


def projected_edit(current: str, tool_input: dict[str, Any]) -> tuple[str, int]:
    old_string = str(tool_input.get("old_string", ""))
    new_string = str(tool_input.get("new_string", ""))
    count = -1 if tool_input.get("replace_all") else 1
    projected = current.replace(old_string, new_string, count) if old_string else current
    offset = projected.find(new_string) if new_string else -1
    first_line = projected.count("\n", 0, offset) + 1 if offset >= 0 else 1
    return projected, first_line


def proposed_content(
    root: Path, path: str, tool_name: str, tool_input: dict[str, Any]
) -> tuple[str, str, int] | None:
    if tool_name == "Write":
        content = str(tool_input.get("content", ""))
        return content, content, 1
    if tool_name == "Edit":
        current = read_text(root / path) or ""
        projected, first_line = projected_edit(current, tool_input)
        return projected, str(tool_input.get("new_string", "")), first_line
    if tool_name == "NotebookEdit":
        new_source = str(tool_input.get("new_source", ""))
        return "", new_source, 1
    return None


def pre_edit_violations(
    root: Path, path: str, tool_name: str, tool_input: dict[str, Any], exemptions: Exemptions
) -> list[Violation]:
    proposal = proposed_content(root, path, tool_name, tool_input)
    if proposal is None:
        return []
    projected, new_text, first_line = proposal
    violations: list[Violation] = []
    if not exemptions.exempts(EM_DASH_RULE, path):
        violations += em_dash_violations(path, new_text, first_line)
    if tool_name == "NotebookEdit":
        return violations
    if not exemptions.exempts(LINE_CAP_RULE, path):
        violations += line_cap_violations(path, projected)
    if is_rule_file(path):
        violations += rule_file_violations(path, projected, repository_files(root))
    return violations


def pre_edit(root: Path, payload: dict[str, Any]) -> HookOutcome:
    tool_input = payload.get("tool_input") or {}
    path = edited_path(root, tool_input)
    if path is None:
        return HookOutcome()
    tool_name = str(payload.get("tool_name", ""))
    violations = pre_edit_violations(root, path, tool_name, tool_input, load_exemptions(root))
    if not violations:
        return HookOutcome()
    reason = "Edit blocked by repository rules:\n" + render_report(violations)
    return HookOutcome(exit_code=BLOCKING_EXIT_CODE, stderr=reason)


def is_introduced(comment_text: str, tool_name: str, tool_input: dict[str, Any]) -> bool:
    if tool_name == "Write":
        return True
    first_line = comment_text.splitlines()[0].strip() if comment_text else ""
    new_string = str(tool_input.get("new_string", ""))
    old_string = str(tool_input.get("old_string", ""))
    return bool(first_line) and first_line in new_string and first_line not in old_string


def post_edit(root: Path, payload: dict[str, Any]) -> HookOutcome:
    tool_name = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input") or {}
    path = edited_path(root, tool_input)
    if path is None or tool_name not in ("Edit", "Write"):
        return HookOutcome()
    if load_exemptions(root).exempts(COMMENT_RULE, path):
        return HookOutcome()
    text = read_text(root / path)
    if text is None or is_generated(text):
        return HookOutcome()
    introduced = [
        comment_violation(path, comment)
        for comment in comments_in(path, text) or []
        if is_introduced(comment.text, tool_name, tool_input)
    ]
    if not introduced:
        return HookOutcome()
    reason = (
        "The edit added comments. Remove them and carry the meaning in names "
        "(rule 6 in AGENTS.md):\n" + render_report(introduced)
    )
    return block_decision(reason)


def stop(root: Path, payload: dict[str, Any]) -> HookOutcome:
    if payload.get("stop_hook_active"):
        return HookOutcome()
    files = repository_files(root)
    rule_files = [path for path in files if matches_glob(path, RULE_FILE_DIRECTORY_GLOB)]
    paths = sorted(set(changed_files(root)) | set(rule_files))
    violations = scan_files(root, paths, files, load_exemptions(root))
    if not violations:
        return HookOutcome()
    reason = "Changed files break repository rules; fix them before finishing:\n" + render_report(
        violations
    )
    return block_decision(reason)
