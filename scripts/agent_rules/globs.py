from __future__ import annotations

import re
from collections.abc import Iterable
from functools import lru_cache

BRACE_GROUP = re.compile(r"\{([^{}]*)\}")


def expand_braces(glob: str) -> list[str]:
    group = BRACE_GROUP.search(glob)
    if group is None:
        return [glob]
    prefix, suffix = glob[: group.start()], glob[group.end() :]
    return [
        expanded
        for alternative in group.group(1).split(",")
        for expanded in expand_braces(prefix + alternative + suffix)
    ]


@lru_cache(maxsize=4096)
def glob_pattern(glob: str) -> re.Pattern[str]:
    pattern = ""
    index = 0
    while index < len(glob):
        if glob.startswith("**/", index):
            pattern += "(?:.*/)?"
            index += 3
        elif glob.startswith("**", index):
            pattern += ".*"
            index += 2
        elif glob[index] == "*":
            pattern += "[^/]*"
            index += 1
        elif glob[index] == "?":
            pattern += "[^/]"
            index += 1
        else:
            pattern += re.escape(glob[index])
            index += 1
    return re.compile(pattern + r"\Z")


def matches_glob(path: str, glob: str) -> bool:
    return any(glob_pattern(alternative).match(path) for alternative in expand_braces(glob))


def matches_any_glob(path: str, globs: Iterable[str]) -> bool:
    return any(matches_glob(path, glob) for glob in globs)
