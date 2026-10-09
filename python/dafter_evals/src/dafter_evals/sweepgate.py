from __future__ import annotations

import json
from pathlib import Path
from typing import Any

BOUNDS = frozenset({"max", "min"})


def _bound(where: str, raw: object) -> dict[str, float]:
    if not isinstance(raw, dict) or not raw or not set(raw) <= BOUNDS:
        raise ValueError(f"{where}: a threshold is max, min or both")
    for value in raw.values():
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(f"{where}: max and min are numbers")
    return {k: float(v) for k, v in raw.items()}


def parse(raw: object) -> dict[str, dict[str, dict[str, dict[str, float]]]]:
    thresholds = raw.get("thresholds") if isinstance(raw, dict) else None
    if not isinstance(thresholds, dict) or not thresholds:
        raise ValueError("a sweep baseline holds thresholds: language, condition, metric path")
    parsed: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    for language, conditions in thresholds.items():
        if not isinstance(conditions, dict) or not conditions:
            raise ValueError(f"thresholds.{language}: conditions, each with metric paths")
        parsed[language] = {}
        for condition, metrics in conditions.items():
            where = f"thresholds.{language}.{condition}"
            if not isinstance(metrics, dict) or not metrics:
                raise ValueError(f"{where}: metric paths, each with a threshold")
            parsed[language][condition] = {
                path: _bound(f"{where}.{path}", bound) for path, bound in metrics.items()
            }
    return parsed


def load(path: Path) -> dict[str, dict[str, dict[str, dict[str, float]]]]:
    try:
        return parse(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read the sweep baseline {path}: {exc}") from exc


def measured(row: object, path: str) -> object:
    value = row
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def within(value: object, bound: dict[str, float]) -> bool:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return False
    return value <= bound.get("max", value) and value >= bound.get("min", value)


def judge(
    report: dict[str, Any], thresholds: dict[str, dict[str, dict[str, dict[str, float]]]]
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    not_run: list[str] = []
    for language, conditions in thresholds.items():
        rows = report["languages"].get(language, {}).get("conditions", {})
        for condition, metrics in conditions.items():
            row = rows.get(condition)
            if row is None:
                not_run.append(f"{language}/{condition}")
                continue
            for path, bound in metrics.items():
                value = measured(row, path)
                checks.append(
                    {
                        "language": language,
                        "condition": condition,
                        "metric": path,
                        "measured": value,
                        **bound,
                        "pass": within(value, bound),
                    }
                )
    return {
        "go": bool(checks) and all(c["pass"] for c in checks),
        "checks": checks,
        "notRun": not_run,
    }
