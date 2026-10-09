from pathlib import Path
from typing import Any

import yaml

PROBE_DIR = Path(__file__).resolve().parent / "probes"
PROBE_LANGUAGES = ("en-IN", "hi", "te-IN", "kn-IN", "mr-IN")


def probe_case(entry: dict[str, Any]) -> dict[str, Any]:
    probe_vars = entry["vars"]
    return {
        "description": f"{probe_vars['language']} {probe_vars['category']}",
        "vars": probe_vars,
        "metadata": {
            "language": probe_vars["language"],
            "category": probe_vars["category"],
            "reviewed": bool(entry.get("reviewed", False)),
        },
    }


def load_probes(config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for language in PROBE_LANGUAGES:
        bank = yaml.safe_load((PROBE_DIR / f"{language}.yaml").read_text(encoding="utf-8")) or {}
        cases.extend(probe_case(entry) for entry in bank.get("tests") or [])
    return cases
