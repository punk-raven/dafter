from __future__ import annotations

import asyncio
import statistics
from dataclasses import asdict, dataclass
from typing import Any

from .probe import Probe, now
from .script import Script
from .voice import Voice

REPLY_TIMEOUT = 15.0
OVERLAP_AFTER = 1.0
STOP_QUIET = 0.4
CONTINUE_WINDOW = 1.5
CONTINUE_GAP = 0.6
SPEAKING_AT_ONSET = 0.3


@dataclass
class TurnResult:
    index: int
    speech_ms: int
    gap_ms: int | None
    endpoint_ms: int | None
    reply_ms: int | None


@dataclass
class OverlapResult:
    kind: str
    text: str
    overlap_at_ms: int
    agent_stopped: bool
    stop_ms: int | None
    states: list[str]
    replied: bool
    inconclusive: bool


def ms(seconds: float | None) -> int | None:
    return None if seconds is None else round(seconds * 1000)


def percentile(values: list[int], p: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(ordered) - 1)
    return round(ordered[lo] + (ordered[hi] - ordered[lo]) * (k - lo))


async def turn(probe: Probe, voice: Voice, index: int, text: str) -> TurnResult:
    pcm = await voice.say(text)
    await probe.settle()
    await asyncio.sleep(0.5)
    spoken = await probe.speak(pcm)
    first_audio = None
    deadline = now() + REPLY_TIMEOUT
    while first_audio is None and now() < deadline:
        first_audio = probe.meter.first_loud_after(spoken.ended)
        await asyncio.sleep(0.01)
    thinking = next((t for t, s in probe.states if t > spoken.started and s == "thinking"), None)
    return TurnResult(
        index=index,
        speech_ms=ms(spoken.ended - spoken.started) or 0,
        gap_ms=ms(first_audio - spoken.ended) if first_audio else None,
        endpoint_ms=ms(thinking - spoken.ended) if thinking else None,
        reply_ms=ms(first_audio - thinking) if first_audio and thinking else None,
    )


async def overlap(
    probe: Probe, voice: Voice, kind: str, prompt: str, text: str, expect_stop: bool
) -> OverlapResult:
    prompt_pcm, pcm = await voice.say(prompt), await voice.say(text)
    await probe.settle()
    await asyncio.sleep(0.5)
    asked = await probe.speak(prompt_pcm)
    first_audio = None
    deadline = now() + REPLY_TIMEOUT
    while first_audio is None and now() < deadline:
        first_audio = probe.meter.first_loud_after(asked.ended)
        await asyncio.sleep(0.01)
    if first_audio is None:
        return OverlapResult(kind, text, 0, False, None, [], False, True)
    await asyncio.sleep(max(0.0, first_audio + OVERLAP_AFTER - now()))
    spoken = await probe.speak(pcm)
    await asyncio.sleep(CONTINUE_WINDOW + STOP_QUIET + 0.2)
    window_end = spoken.started + CONTINUE_WINDOW
    quiet_from = probe.meter.quiet_run_start(spoken.started, window_end + STOP_QUIET, STOP_QUIET)
    stopped = quiet_from is not None and quiet_from <= window_end
    if not expect_stop:
        gap = probe.meter.quiet_run_start(spoken.started, window_end, CONTINUE_GAP)
        stopped = gap is not None
    states = [s for t, s in probe.states if spoken.started < t <= window_end + STOP_QUIET]
    resumed = probe.meter.loud_since(window_end + STOP_QUIET)
    last_before = probe.meter.last_loud_before(spoken.started)
    speaking = last_before is not None and spoken.started - last_before < SPEAKING_AT_ONSET
    ended_naturally = stopped and not expect_stop and "listening" in states and not resumed
    inconclusive = not speaking or ended_naturally
    stop_ms = ms(quiet_from - spoken.started) if stopped and quiet_from else None
    return OverlapResult(
        kind=kind,
        text=text,
        overlap_at_ms=ms(spoken.started - first_audio) or 0,
        agent_stopped=stopped,
        stop_ms=stop_ms,
        states=states,
        replied="thinking" in states,
        inconclusive=inconclusive,
    )


SCENARIOS = ("turns", "barge_in", "backchannel", "filler")


async def run(
    probe: Probe, voice: Voice, script: Script, turns: int, scenarios: frozenset[str]
) -> dict[str, Any]:
    await probe.wait_agent_audio(REPLY_TIMEOUT)
    results: list[TurnResult] = []
    if "turns" in scenarios:
        for i, text in enumerate(script.turns[:turns]):
            results.append(await turn(probe, voice, i, text))
    plans = (
        ("barge_in", script.interruptions, True),
        ("backchannel", script.backchannels, False),
        ("filler", script.fillers, False),
    )
    overlaps: list[OverlapResult] = []
    for kind, texts, expect_stop in plans:
        if kind not in scenarios:
            continue
        for prompt, text in zip(script.long_prompts, texts, strict=False):
            overlaps.append(await overlap(probe, voice, kind, prompt, text, expect_stop))
    return summarize(results, overlaps, probe.event_errors)


def summarize(
    results: list[TurnResult], overlaps: list[OverlapResult], event_errors: int
) -> dict[str, Any]:
    gaps = [r.gap_ms for r in results if r.gap_ms is not None]
    endpoints = [r.endpoint_ms for r in results if r.endpoint_ms is not None]
    replies = [r.reply_ms for r in results if r.reply_ms is not None]
    summary: dict[str, Any] = {
        "turns": len(results),
        "answered": len(gaps),
        "gap_p50_ms": percentile(gaps, 0.5),
        "gap_p95_ms": percentile(gaps, 0.95),
        "gap_max_ms": max(gaps) if gaps else None,
        "gap_stdev_ms": round(statistics.pstdev(gaps)) if len(gaps) > 1 else None,
        "endpoint_p50_ms": percentile(endpoints, 0.5),
        "endpoint_p95_ms": percentile(endpoints, 0.95),
        "reply_p50_ms": percentile(replies, 0.5),
        "reply_p95_ms": percentile(replies, 0.95),
        "event_errors": event_errors,
    }
    for kind in ("barge_in", "backchannel", "filler"):
        rows = [o for o in overlaps if o.kind == kind]
        stops = [o.stop_ms for o in rows if o.stop_ms is not None]
        summary[kind] = {
            "trials": len(rows),
            "stopped": sum(o.agent_stopped for o in rows),
            "inconclusive": sum(o.inconclusive for o in rows),
            "replied_to": sum(o.replied for o in rows),
            "stop_p50_ms": percentile(stops, 0.5),
            "stop_max_ms": max(stops) if stops else None,
        }
    return {
        "summary": summary,
        "turns": [asdict(r) for r in results],
        "overlaps": [asdict(o) for o in overlaps],
    }
