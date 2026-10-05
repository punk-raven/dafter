from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from dafter_core.config import ProviderRef, ResolvedSessionConfig
from dafter_core.enums import Stage, UsageUnit
from dafter_providers import vendor_for
from dafter_runtime.cost import Price, load_prices
from dafter_runtime.personas import base_language
from livekit.agents import stt

from .corpus import Clip, Sample
from .hearing import TAIL_S, Heard
from .measure import percentile
from .wer import Score, word_error_rate, words

KIND = "dafter.asr.accuracy"
IDENTIFY = "identify"

Hear = Callable[[Clip], Awaitable[Heard]]


@dataclass(frozen=True, slots=True)
class Setup:
    ref: ProviderRef
    language: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.ref.provider,
            "model": self.ref.model,
            "mode": self.ref.options.get("mode"),
            "hearing": self.language or IDENTIFY,
        }


def setup(cfg: ResolvedSessionConfig, sample: Sample, mode: str | None, identify: bool) -> Setup:
    pipeline = cfg.agent.pipeline
    if pipeline is None or pipeline.stt is None:
        raise ValueError("the job names no STT")
    ref = pipeline.stt
    if mode is not None:
        ref = replace(ref, options={**ref.options, "mode": mode})
    return Setup(ref, None if identify else sample.language)


def recognizer(s: Setup, cfg: ResolvedSessionConfig) -> stt.STT[Any]:
    build = vendor_for(s.ref, Stage.STT).stt
    if build is None:
        raise ValueError(f"{s.ref.provider} has no STT")
    return build(s.ref, s.language, cfg.turn, None)


def price_of(s: Setup) -> Price | None:
    return load_prices().get((s.ref.provider, s.ref.model or "", UsageUnit.AUDIO_SECOND))


def streamed_seconds(sample: Sample) -> float:
    return sum(c.duration_s + TAIL_S for c in sample.clips)


def estimate(s: Setup, sample: Sample) -> Decimal | None:
    price = price_of(s)
    return price.cost(streamed_seconds(sample)) if price is not None else None


def latin_words(text: str) -> int:
    return sum(1 for w in words(text) if w.isascii() and any(c.isalpha() for c in w))


def identified(sample: Sample, heard: Heard) -> bool | None:
    if not heard.languages:
        return None
    wanted = base_language(sample.language)
    return all(base_language(language) == wanted for language in heard.languages)


def clip_row(clip: Clip, heard: Heard, result: Score, sample: Sample) -> dict[str, Any]:
    return {
        "id": clip.id,
        "speaker": clip.speaker,
        "durationS": clip.duration_s,
        "reference": clip.reference,
        "hypothesis": heard.text,
        **result.to_dict(),
        "latinWords": latin_words(heard.text),
        "languages": list(heard.languages),
        "languageConfidences": list(heard.confidences),
        "identified": identified(sample, heard),
        "finalAfterAudioMs": heard.final_after_audio_ms,
    }


def summary(rows: list[dict[str, Any]], s: Setup, sample: Sample, failed: int) -> dict[str, Any]:
    words = sum(r["referenceWords"] for r in rows)
    kinds = {k: sum(r[k] for r in rows) for k in ("substitutions", "deletions", "insertions")}
    judged = [r["identified"] for r in rows if r["identified"] is not None]
    finals = [r["finalAfterAudioMs"] for r in rows if r["finalAfterAudioMs"] is not None]
    cost = estimate(s, sample)
    return {
        "clips": len(rows),
        "failed": failed,
        "referenceWords": words,
        "wer": round(sum(kinds.values()) / words, 4) if words else None,
        **kinds,
        "latinWords": sum(r["latinWords"] for r in rows),
        "identifiedCorrectly": sum(judged) if s.language is None else None,
        "identifiedOf": len(judged) if s.language is None else None,
        "finalAfterAudioMs": {"p50": percentile(finals, 0.5), "p95": percentile(finals, 0.95)},
        "audioSeconds": round(streamed_seconds(sample), 2),
        "costInr": float(cost) if cost is not None else None,
    }


async def measure(sample: Sample, s: Setup, hear: Hear, concurrency: int) -> dict[str, Any]:
    gate = asyncio.Semaphore(concurrency)

    async def one(clip: Clip) -> dict[str, Any] | None:
        async with gate:
            try:
                heard = await hear(clip)
            except Exception as exc:
                return {"id": clip.id, "error": type(exc).__name__}
        return clip_row(clip, heard, word_error_rate(clip.reference, heard.text), sample)

    results = [r for r in await asyncio.gather(*(one(c) for c in sample.clips)) if r]
    rows = [r for r in results if "error" not in r]
    failures = [r for r in results if "error" in r]
    return {
        "kind": KIND,
        "ranAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "dataset": sample.dataset,
        "language": sample.language,
        "source": sample.source,
        "stt": s.to_dict(),
        "summary": summary(rows, s, sample, len(failures)),
        "clips": rows,
        "failures": failures,
    }


__all__ = ["IDENTIFY", "KIND", "Setup", "estimate", "measure", "recognizer", "setup"]
