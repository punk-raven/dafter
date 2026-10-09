from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .golden import GoldenClip
from .measure import percentile
from .probe import Probe, now

PAUSE_BUDGETS_MS = (300, 600)
HOLD = "hold"
END = "end"
TURN_LABELS = {f"turn:{HOLD}": HOLD, f"turn:{END}": END}
BUCKET_KINDS: dict[str, frozenset[str]] = {
    "digit_string": frozenset({"number", "phone", "amount"}),
    "address": frozenset({"address"}),
    "spelled_name": frozenset({"name"}),
}
REPLY_TIMEOUT_S = 15.0
SETTLE_PAUSE_S = 0.5
POLL_S = 0.01
TAKES_THE_TURN = "thinking"
DECIMALS = 4


@dataclass(frozen=True, slots=True)
class TurnTrial:
    clip_id: str
    label: str
    buckets: tuple[str, ...]
    take_ms: int | None
    dead_air_ms: int | None

    def cut_off(self, budget_ms: int) -> bool:
        if self.take_ms is None:
            return False
        return self.take_ms < 0 or (self.label == HOLD and self.take_ms < budget_ms)


def turn_label(clip: GoldenClip) -> str | None:
    return next((TURN_LABELS[label] for label in clip.labels if label in TURN_LABELS), None)


def buckets_of(clip: GoldenClip) -> tuple[str, ...]:
    kinds = {entity.kind for entity in clip.entities}
    return tuple(name for name, members in BUCKET_KINDS.items() if kinds & members)


def turn_clips(clips: Iterable[GoldenClip]) -> list[GoldenClip]:
    return [clip for clip in clips if turn_label(clip) is not None]


def _ms(seconds: float | None) -> int | None:
    return None if seconds is None else round(seconds * 1000)


async def replay(probe: Probe, clip: GoldenClip, pcm: np.ndarray) -> TurnTrial:
    label = turn_label(clip)
    if label is None:
        raise ValueError(f"{clip.clip_id} carries no turn label")
    await probe.settle()
    await asyncio.sleep(SETTLE_PAUSE_S)
    spoken = await probe.speak(pcm)
    first_audio = None
    deadline = now() + REPLY_TIMEOUT_S
    while first_audio is None and now() < deadline:
        first_audio = probe.meter.first_loud_after(spoken.started)
        await asyncio.sleep(POLL_S)
    taken = next(
        (t for t, s in probe.states if t > spoken.started and s == TAKES_THE_TURN), first_audio
    )
    return TurnTrial(
        clip_id=clip.clip_id,
        label=label,
        buckets=buckets_of(clip),
        take_ms=_ms(taken - spoken.ended) if taken is not None else None,
        dead_air_ms=_ms(first_audio - spoken.ended) if first_audio is not None else None,
    )


def _rate(hits: int, trials: int) -> float | None:
    return round(hits / trials, DECIMALS) if trials else None


def cutoff_rates(trials: list[TurnTrial]) -> dict[str, Any]:
    return {
        "trials": len(trials),
        "falseCutoffRate": {
            f"{budget}ms": _rate(sum(t.cut_off(budget) for t in trials), len(trials))
            for budget in PAUSE_BUDGETS_MS
        },
        "cutInsideClip": sum(1 for t in trials if t.take_ms is not None and t.take_ms < 0),
    }


def summarize(trials: list[TurnTrial]) -> dict[str, Any]:
    holds = [t for t in trials if t.label == HOLD]
    ends = [t for t in trials if t.label == END]
    dead_air = [t.dead_air_ms for t in ends if t.dead_air_ms is not None and t.dead_air_ms >= 0]
    return {
        "hold": cutoff_rates(holds),
        "end": {
            "trials": len(ends),
            "answered": len(dead_air),
            "deadAirP50Ms": percentile(dead_air, 0.5),
            "deadAirP95Ms": percentile(dead_air, 0.95),
            "cutInsideClip": sum(1 for t in ends if t.take_ms is not None and t.take_ms < 0),
        },
        "buckets": {
            name: cutoff_rates([t for t in trials if name in t.buckets]) for name in BUCKET_KINDS
        },
        "pauseBudgetsMs": list(PAUSE_BUDGETS_MS),
        "trialsDetail": [asdict(t) for t in trials],
    }
