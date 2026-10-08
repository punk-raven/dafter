from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from agent_rules.globs import matches_any_glob

EXEMPTIONS_FILE = Path(__file__).resolve().parent / "exemptions.txt"
SKILLS_LOCK_FILE = "skills-lock.json"
ALL_RULES = "*"


@dataclass
class Exemptions:
    globs_by_rule: dict[str, list[str]] = field(default_factory=dict)

    def exempts(self, rule: str, path: str) -> bool:
        globs = self.globs_by_rule.get(rule, []) + self.globs_by_rule.get(ALL_RULES, [])
        return matches_any_glob(path, globs)


def third_party_skill_globs(root: Path) -> list[str]:
    lock_path = root / SKILLS_LOCK_FILE
    if not lock_path.is_file():
        return []
    skills = json.loads(lock_path.read_text(encoding="utf-8")).get("skills", {})
    return [f".agents/skills/{name}/**" for name in sorted(skills)]


def load_exemptions(root: Path, exemptions_file: Path = EXEMPTIONS_FILE) -> Exemptions:
    globs_by_rule: dict[str, list[str]] = {ALL_RULES: third_party_skill_globs(root)}
    for entry in exemptions_file.read_text(encoding="utf-8").splitlines():
        if not entry.strip():
            continue
        rule, glob = entry.split(maxsplit=1)
        globs_by_rule.setdefault(rule, []).append(glob.strip())
    return Exemptions(globs_by_rule)
