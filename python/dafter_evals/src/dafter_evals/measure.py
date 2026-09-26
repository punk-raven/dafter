from __future__ import annotations

from typing import Any

from dafter_core.config import Budgets

LAYERS = (
    "endOfTurnDelayMs",
    "transcriptionDelayMs",
    "llmNodeTtftMs",
    "llmNodeTtfsMs",
    "ttsNodeTtfbMs",
    "playbackLatencyMs",
    "e2eLatencyMs",
)
REPLY_LAYER = "e2eLatencyMs"
AGENT_SPEAKING = "speaking"


def percentile(values: list[int], p: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo))


def spread(values: list[int]) -> dict[str, int | None]:
    return {"n": len(values), "p50": percentile(values, 0.5), "p95": percentile(values, 0.95)}


def layers(turn_metrics: list[dict[str, Any]]) -> dict[str, dict[str, int | None]]:
    return {
        layer: spread([m[layer] for m in turn_metrics if isinstance(m.get(layer), int)])
        for layer in LAYERS
    }


def state_stop(states: list[tuple[float, str]], onset: float, until: float) -> int | None:
    for t, state in states:
        if onset < t <= until and state != AGENT_SPEAKING:
            return round((t - onset) * 1000)
    return None


def _check(measured: int | None, budget: int, missed: int = 0) -> dict[str, Any]:
    return {
        "measuredMs": measured,
        "budgetMs": budget,
        "pass": measured is not None and measured <= budget and missed == 0,
    }


def verdict(
    by_layer: dict[str, dict[str, int | None]], barge_in: dict[str, Any], budgets: Budgets
) -> dict[str, Any]:
    reply = by_layer[REPLY_LAYER]
    unstopped = barge_in["trials"] - barge_in["inconclusive"] - barge_in["state_stopped"]
    checks = {
        "replyP50": _check(reply["p50"], budgets.turn_gap_p50_ms),
        "replyP95": _check(reply["p95"], budgets.turn_gap_p95_ms),
        "bargeInStopP50": _check(
            barge_in["state_stop_p50_ms"], budgets.barge_in_stop_p50_ms, unstopped
        ),
    }
    checks["bargeInStopP50"]["unstopped"] = unstopped
    return {"go": all(c["pass"] for c in checks.values()), "checks": checks}


def _delta(before: int | None, after: int | None) -> int | None:
    return None if before is None or after is None else after - before


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for layer in LAYERS:
        was, now = baseline["layers"][layer], current["layers"][layer]
        rows[layer] = {q: _delta(was[q], now[q]) for q in ("p50", "p95")}
    was_stop = baseline["barge_in"]["state_stop_p50_ms"]
    now_stop = current["barge_in"]["state_stop_p50_ms"]
    rows["bargeInStopP50Ms"] = _delta(was_stop, now_stop)
    return {
        "deltaMs": rows,
        "go": {"before": baseline["verdict"]["go"], "after": current["verdict"]["go"]},
    }
