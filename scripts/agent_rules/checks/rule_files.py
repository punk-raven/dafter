from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from agent_rules.globs import expand_braces, glob_pattern, matches_glob
from agent_rules.violation import Violation

RULE = "rule-file"
RULE_FILE_GLOB = ".agents/rules/**/*.md"
FRONTMATTER_FENCE = "---"
PATHS_KEY = "paths:"
LIST_ITEM_PREFIX = "- "
QUOTES = ('"', "'")


@dataclass
class Frontmatter:
    globs: list[str] = field(default_factory=list)
    problems: list[tuple[int, str]] = field(default_factory=list)


def is_rule_file(path: str) -> bool:
    return matches_glob(path, RULE_FILE_GLOB)


def unquote(value: str) -> str | None:
    if len(value) >= 2 and value[0] in QUOTES and value[-1] == value[0]:
        return value[1:-1]
    return None


def frontmatter_lines(text: str) -> list[str] | None:
    lines = text.splitlines()
    if not lines or lines[0].strip() != FRONTMATTER_FENCE:
        return None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == FRONTMATTER_FENCE:
            return lines[1:index]
    return None


def parse_inline_paths(value: str, line_number: int, frontmatter: Frontmatter) -> None:
    unquoted = unquote(value)
    if unquoted is None:
        frontmatter.problems.append((line_number, "quote the comma-separated paths value"))
        return
    frontmatter.globs.extend(glob.strip() for glob in unquoted.split(",") if glob.strip())


def parse_frontmatter(text: str) -> Frontmatter:
    frontmatter = Frontmatter()
    lines = frontmatter_lines(text)
    if lines is None:
        frontmatter.problems.append(
            (1, "missing --- frontmatter; the file would load every session")
        )
        return frontmatter
    seen_paths = False
    for line_number, line in enumerate(lines, start=2):
        stripped = line.strip()
        if not stripped:
            continue
        if line.startswith(PATHS_KEY):
            seen_paths = True
            inline = line[len(PATHS_KEY) :].strip()
            if inline:
                parse_inline_paths(inline, line_number, frontmatter)
        elif seen_paths and stripped.startswith(LIST_ITEM_PREFIX) and line[:1].isspace():
            glob = unquote(stripped[len(LIST_ITEM_PREFIX) :].strip())
            if glob is None:
                frontmatter.problems.append((line_number, "quote every glob in paths"))
            else:
                frontmatter.globs.append(glob)
        else:
            frontmatter.problems.append((line_number, f"unexpected frontmatter line: {stripped}"))
    if not seen_paths or not frontmatter.globs:
        frontmatter.problems.append(
            (1, "paths is missing or empty; the file would load every session")
        )
    return frontmatter


def unmatched_alternatives(glob: str, repository_files: Sequence[str]) -> list[str]:
    return [
        alternative
        for alternative in expand_braces(glob)
        if not any(glob_pattern(alternative).match(path) for path in repository_files)
    ]


def rule_file_violations(path: str, text: str, repository_files: Sequence[str]) -> list[Violation]:
    frontmatter = parse_frontmatter(text)
    violations = [Violation(path, line, RULE, message) for line, message in frontmatter.problems]
    for glob in frontmatter.globs:
        for alternative in unmatched_alternatives(glob, repository_files):
            violations.append(Violation(path, 1, RULE, f"glob matches no file: {alternative}"))
    return violations
