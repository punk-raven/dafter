from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .grade import Outcome
from .passk import curve, suite_pass_hat_k
from .run import OVER_CAP, Played


@dataclass(frozen=True, slots=True)
class Gates:
    k: int
    min_pass_k: float = 0.85
    min_tool_success: float = 0.99
    min_entity_accuracy: float = 0.98


def _ratio(hits: int, total: int) -> float | None:
    return hits / total if total else None


def metrics(outcomes: Mapping[str, Sequence[Outcome]], k: int) -> dict[str, Any]:
    flat = [o for found in outcomes.values() for o in found]
    results = {task: [o.passed for o in found] for task, found in outcomes.items()}
    calls = sum(o.tool_calls for o in flat)
    errors = sum(o.tool_errors for o in flat)
    entities = [e for o in flat for e in o.entities]
    return {
        "tasks": len(outcomes),
        "trials": len(flat),
        "passK": suite_pass_hat_k(results, k) if results else None,
        "curve": curve(results, k) if results else {},
        "trialSuccess": _ratio(sum(o.passed for o in flat), len(flat)),
        "toolCalls": calls,
        "toolSuccess": _ratio(calls - errors, calls),
        "entities": len(entities),
        "entityAccuracy": _ratio(sum(1 for e in entities if e["heard"]), len(entities)),
    }


def _at_least(value: float | None, floor: float) -> bool:
    return value is None or value >= floor


def gate(found: Mapping[str, Any], gates: Gates, skipped: Mapping[str, str]) -> dict[str, Any]:
    checks = {
        "passK": found["passK"] is not None and found["passK"] >= gates.min_pass_k,
        "toolSuccess": _at_least(found["toolSuccess"], gates.min_tool_success),
        "entityAccuracy": _at_least(found["entityAccuracy"], gates.min_entity_accuracy),
        "complete": OVER_CAP not in skipped.values(),
    }
    return {
        "pass": all(checks.values()),
        "checks": checks,
        "thresholds": {
            "k": gates.k,
            "passK": gates.min_pass_k,
            "toolSuccess": gates.min_tool_success,
            "entityAccuracy": gates.min_entity_accuracy,
        },
    }


def summarize(played: Played, gates: Gates) -> dict[str, Any]:
    by_language: dict[str, dict[str, list[Outcome]]] = {}
    for task_id, outcomes in played.outcomes.items():
        language = played.tasks[task_id].language
        by_language.setdefault(language, {})[task_id] = outcomes
    languages = {
        language: {**metrics(found, gates.k), "gate": gate(metrics(found, gates.k), gates, {})}
        for language, found in by_language.items()
    }
    overall = metrics(played.outcomes, gates.k)
    per_task = {
        task_id: {
            "language": played.tasks[task_id].language,
            "reviewed": played.tasks[task_id].reviewed,
            "passed": sum(o.passed for o in outcomes),
            "trials": len(outcomes),
            "passK": suite_pass_hat_k({task_id: [o.passed for o in outcomes]}, gates.k),
        }
        for task_id, outcomes in played.outcomes.items()
    }
    unreviewed = sorted(t.id for t in played.tasks.values() if not t.reviewed)
    any_failed = any(not g["gate"]["pass"] for g in languages.values())
    verdict = gate(overall, gates, played.skipped)
    if any_failed:
        verdict = {**verdict, "pass": False, "checks": {**verdict["checks"], "perLanguage": False}}
    return {
        "overall": overall,
        "byLanguage": languages,
        "byTask": per_task,
        "skipped": dict(played.skipped),
        "unreviewed": unreviewed,
        "gate": verdict,
    }


def _shown(value: float | None) -> str:
    return "-" if value is None else f"{value:.2f}"


def table(summary: Mapping[str, Any], k: int) -> str:
    rows = [
        f"| Language | Tasks | Trials | pass^{k} | Trial success | Tool success | Entities "
        "| Gate |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for language, found in summary["byLanguage"].items():
        rows.append(
            f"| {language} | {found['tasks']} | {found['trials']} | {_shown(found['passK'])} | "
            f"{_shown(found['trialSuccess'])} | {_shown(found['toolSuccess'])} | "
            f"{_shown(found['entityAccuracy'])} | {'pass' if found['gate']['pass'] else 'FAIL'} |"
        )
    overall = summary["overall"]
    rows.append(
        f"| all | {overall['tasks']} | {overall['trials']} | {_shown(overall['passK'])} | "
        f"{_shown(overall['trialSuccess'])} | {_shown(overall['toolSuccess'])} | "
        f"{_shown(overall['entityAccuracy'])} | "
        f"{'pass' if summary['gate']['pass'] else 'FAIL'} |"
    )
    lines = ["\n".join(rows), ""]
    failing = [t for t, v in summary["byTask"].items() if v["passed"] < v["trials"]]
    if failing:
        lines.append("Tasks with a failed trial: " + ", ".join(failing))
    if summary["skipped"]:
        lines += [f"Skipped {t}: {why}" for t, why in summary["skipped"].items()]
    if summary["unreviewed"]:
        lines.append(
            f"{len(summary['unreviewed'])} tasks are drafts no native speaker has reviewed yet."
        )
    return "\n".join(lines) + "\n"
