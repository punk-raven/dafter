from __future__ import annotations

import statistics
from typing import Any

from dafter_core.config import Budgets

CALLER = "caller"
WORKER = "worker"
CLOCKS = {
    CALLER: (
        "the probe's: from the caller's last word, or a barge-in's onset, as the probe sent it,"
        " to what the probe received back"
    ),
    WORKER: (
        "the worker's: from the end of speech its STT reported; an STT whose end of speech carries"
        " no speech end time is anchored at that event's arrival, after the caller's last word,"
        " and its user-side layers then read near 0"
    ),
}
CALLER_SPANS = ("gap_ms", "end_of_turn_ms", "reply_ms")
LAYERS = (
    "endOfTurnDelayMs",
    "transcriptionDelayMs",
    "llmNodeTtftMs",
    "llmNodeTtfsMs",
    "ttsNodeTtfbMs",
    "playbackLatencyMs",
    "e2eLatencyMs",
)
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


def caller(gaps: list[int], ends: list[int], replies: list[int]) -> dict[str, Any]:
    gap: dict[str, int | None] = spread(gaps)
    gap["max"] = max(gaps) if gaps else None
    gap["stdev"] = round(statistics.pstdev(gaps)) if len(gaps) > 1 else None
    return {
        "clock": CALLER,
        "gap_ms": gap,
        "end_of_turn_ms": spread(ends),
        "reply_ms": spread(replies),
    }


def worker(turn_metrics: list[dict[str, Any]]) -> dict[str, Any]:
    layers = {
        layer: spread([m[layer] for m in turn_metrics if isinstance(m.get(layer), int)])
        for layer in LAYERS
    }
    return {"clock": WORKER, "layers": layers}


def state_stop(states: list[tuple[float, str]], onset: float, until: float) -> int | None:
    for t, state in states:
        if onset < t <= until and state != AGENT_SPEAKING:
            return round((t - onset) * 1000)
    return None


def _check(measures: str, measured: int | None, budget: int, missed: int = 0) -> dict[str, Any]:
    return {
        "measures": measures,
        "clock": CALLER,
        "measuredMs": measured,
        "budgetMs": budget,
        "pass": measured is not None and measured <= budget and missed == 0,
    }


def verdict(
    by_caller: dict[str, Any], barge_in: dict[str, Any], budgets: Budgets
) -> dict[str, Any]:
    gap = by_caller["gap_ms"]
    unstopped = barge_in["trials"] - barge_in["inconclusive"] - barge_in["state_stopped"]
    checks = {
        "replyP50": _check("caller.gap_ms.p50", gap["p50"], budgets.turn_gap_p50_ms),
        "replyP95": _check("caller.gap_ms.p95", gap["p95"], budgets.turn_gap_p95_ms),
        "bargeInStopP50": _check(
            "barge_in.state_stop_p50_ms",
            barge_in["state_stop_p50_ms"],
            budgets.barge_in_stop_p50_ms,
            unstopped,
        ),
    }
    checks["bargeInStopP50"]["unstopped"] = unstopped
    return {"go": all(c["pass"] for c in checks.values()), "checks": checks}


def _delta(before: int | None, after: int | None) -> int | None:
    return None if before is None or after is None else after - before


def _deltas(was: dict[str, Any], now: dict[str, Any]) -> dict[str, int | None]:
    return {q: _delta(was[q], now[q]) for q in ("p50", "p95")}


def compare(baseline: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    was_caller, now_caller = baseline[CALLER], current[CALLER]
    was_layers, now_layers = baseline[WORKER]["layers"], current[WORKER]["layers"]
    rows: dict[str, Any] = {
        CALLER: {s: _deltas(was_caller[s], now_caller[s]) for s in CALLER_SPANS},
        WORKER: {layer: _deltas(was_layers[layer], now_layers[layer]) for layer in LAYERS},
    }
    was_stop = baseline["barge_in"]["state_stop_p50_ms"]
    now_stop = current["barge_in"]["state_stop_p50_ms"]
    rows["bargeInStopP50Ms"] = _delta(was_stop, now_stop)
    return {
        "deltaMs": rows,
        "go": {"before": baseline["verdict"]["go"], "after": current["verdict"]["go"]},
    }
