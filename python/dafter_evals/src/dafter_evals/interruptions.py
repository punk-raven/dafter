from __future__ import annotations

import asyncio
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from .golden import GoldenClip
from .measure import percentile
from .probe import Probe, now

INTERRUPTION = "interruption"
BACKCHANNEL = "backchannel"
OVERLAP_KINDS = (INTERRUPTION, BACKCHANNEL)
REPLY_TIMEOUT_S = 15.0
SETTLE_PAUSE_S = 0.5
OVERLAP_AFTER_S = 1.0
STOP_WINDOW_S = 1.5
STOP_QUIET_S = 0.4
RESUME_WINDOW_S = 2.0
SPEAKING_AT_ONSET_S = 0.3
POLL_S = 0.01
REPLYING = "thinking"
DECIMALS = 4


@dataclass(frozen=True, slots=True)
class InterruptionTrial:
    clip_id: str
    kind: str
    label_class: str
    stopped: bool
    stop_ms: int | None
    resumed: bool
    replied: bool
    inconclusive: bool

    @property
    def handled_as_backchannel(self) -> bool:
        return (not self.stopped or self.resumed) and not self.replied


def overlap_label(clip: GoldenClip) -> tuple[str, str] | None:
    for label in clip.labels:
        kind, _, label_class = label.partition(":")
        if kind in OVERLAP_KINDS:
            return kind, label_class
    return None


def overlap_clips(clips: Iterable[GoldenClip]) -> list[GoldenClip]:
    return [clip for clip in clips if overlap_label(clip) is not None]


def _ms(seconds: float | None) -> int | None:
    return None if seconds is None else round(seconds * 1000)


async def _first_agent_audio(probe: Probe, after: float) -> float | None:
    deadline = now() + REPLY_TIMEOUT_S
    while now() < deadline:
        heard = probe.meter.first_loud_after(after)
        if heard is not None:
            return heard
        await asyncio.sleep(POLL_S)
    return None


async def replay(
    probe: Probe, clip: GoldenClip, prompt: np.ndarray, pcm: np.ndarray
) -> InterruptionTrial:
    labelled = overlap_label(clip)
    if labelled is None:
        raise ValueError(f"{clip.clip_id} carries no interruption or backchannel label")
    kind, label_class = labelled
    await probe.settle()
    await asyncio.sleep(SETTLE_PAUSE_S)
    asked = await probe.speak(prompt)
    answer = await _first_agent_audio(probe, asked.ended)
    if answer is None:
        return InterruptionTrial(clip.clip_id, kind, label_class, False, None, False, False, True)
    await asyncio.sleep(max(0.0, answer + OVERLAP_AFTER_S - now()))
    spoken = await probe.speak(pcm)
    window_end = spoken.started + STOP_WINDOW_S
    await asyncio.sleep(max(0.0, window_end + STOP_QUIET_S + RESUME_WINDOW_S - now()))
    meter = probe.meter
    quiet_from = meter.quiet_run_start(spoken.started, window_end + STOP_QUIET_S, STOP_QUIET_S)
    stopped = quiet_from is not None and quiet_from <= window_end
    states = [s for t, s in probe.states if t > spoken.started]
    replied = REPLYING in states
    resumed = False
    if stopped and quiet_from is not None and not replied:
        back = meter.first_loud_after(quiet_from + STOP_QUIET_S)
        resumed = back is not None and back <= quiet_from + STOP_QUIET_S + RESUME_WINDOW_S
    last_before = meter.last_loud_before(spoken.started)
    speaking = last_before is not None and spoken.started - last_before < SPEAKING_AT_ONSET_S
    return InterruptionTrial(
        clip_id=clip.clip_id,
        kind=kind,
        label_class=label_class,
        stopped=stopped,
        stop_ms=_ms(quiet_from - spoken.started) if stopped and quiet_from is not None else None,
        resumed=resumed,
        replied=replied,
        inconclusive=not speaking,
    )


def _rate(hits: int, trials: int) -> float | None:
    return round(hits / trials, DECIMALS) if trials else None


def _classes(trials: list[InterruptionTrial]) -> dict[str, dict[str, int]]:
    classes: dict[str, dict[str, int]] = {}
    for trial in trials:
        row = classes.setdefault(trial.label_class, {"trials": 0, "stopped": 0})
        row["trials"] += 1
        row["stopped"] += trial.stopped
    return classes


def summarize(trials: list[InterruptionTrial]) -> dict[str, Any]:
    conclusive = [t for t in trials if not t.inconclusive]
    barge_ins = [t for t in conclusive if t.kind == INTERRUPTION]
    backchannels = [t for t in conclusive if t.kind == BACKCHANNEL]
    stops = [t.stop_ms for t in barge_ins if t.stop_ms is not None]
    false_stops = [t for t in backchannels if t.stopped]
    return {
        "trials": len(trials),
        "inconclusive": len(trials) - len(conclusive),
        "interruptions": len(barge_ins),
        "backchannels": len(backchannels),
        "missedInterruptionRate": _rate(sum(not t.stopped for t in barge_ins), len(barge_ins)),
        "falseBargeInRate": _rate(len(false_stops), len(backchannels)),
        "backchannelRecall": _rate(
            sum(t.handled_as_backchannel for t in backchannels), len(backchannels)
        ),
        "resumeSuccess": _rate(sum(t.resumed for t in false_stops), len(false_stops)),
        "tStopP50Ms": percentile(stops, 0.5),
        "tStopP95Ms": percentile(stops, 0.95),
        "classes": {
            INTERRUPTION: _classes(barge_ins),
            BACKCHANNEL: _classes(backchannels),
        },
        "trialsDetail": [asdict(t) for t in trials],
    }
