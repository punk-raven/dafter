from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from dafter_core.config import ProviderRef, ResolvedSessionConfig
from dafter_core.enums import Stage, UsageUnit
from dafter_providers import fallback, vendor_for
from livekit.agents import tts as lk_tts

from ..tts import priced

BuildVoice = Callable[[ProviderRef, str], tuple[lk_tts.TTS[Any], fallback.Classify]]


@dataclass(frozen=True, slots=True)
class VoiceChain:
    primary: ProviderRef
    fallbacks: tuple[ProviderRef, ...]

    @property
    def refs(self) -> tuple[ProviderRef, ...]:
        return (self.primary, *self.fallbacks)

    def cost_inr(self, characters: int) -> Decimal | None:
        costs = [priced(ref, UsageUnit.CHARACTER, characters) for ref in self.refs]
        if any(c is None for c in costs):
            return None
        return max(c for c in costs if c is not None)


def chain_of(cfg: ResolvedSessionConfig) -> VoiceChain:
    pipeline = cfg.agent.pipeline
    if pipeline is None or pipeline.tts is None:
        raise ValueError("the job names no TTS")
    return VoiceChain(primary=pipeline.tts, fallbacks=pipeline.fallback.tts)


def build_voice(ref: ProviderRef, language: str) -> tuple[lk_tts.TTS[Any], fallback.Classify]:
    vendor = vendor_for(ref, Stage.TTS)
    if vendor.tts is None:
        raise ValueError(f"{ref.provider} has no TTS")
    return vendor.tts(ref, language), vendor.classify


@dataclass
class Spoken:
    replies: int = 0
    voiced: int = 0
    characters: int = 0
    switches: int = 0

    @property
    def all_voiced(self) -> bool:
        return self.voiced == self.replies

    def to_dict(self) -> dict[str, int]:
        return {
            "replies": self.replies,
            "voiced": self.voiced,
            "characters": self.characters,
            "switches": self.switches,
        }


class Voice:
    def __init__(
        self,
        chain: VoiceChain,
        language: str,
        faulted: bool,
        build: BuildVoice = build_voice,
    ) -> None:
        built = [build(ref, language) for ref in chain.refs]
        self.primary = built[0][0]
        self.spoken = Spoken()
        self._voice = fallback.tts(
            built[0][0], [b[0] for b in built[1:]], faulted, [b[1] for b in built]
        )
        self._made = [b[0] for b in built]
        if self._voice is not self.primary:
            fallback.follow_switches(self._voice, Stage.TTS, self._switched)

    def _switched(self) -> None:
        self.spoken.switches += 1

    async def say(self, text: str) -> bool:
        if not text.strip():
            return True
        self.spoken.replies += 1
        self.spoken.characters += len(text)
        try:
            async with self._voice.synthesize(text) as stream:
                frame = await stream.collect()
        except Exception:
            return False
        if frame.samples_per_channel <= 0:
            return False
        self.spoken.voiced += 1
        return True

    async def aclose(self) -> None:
        if self._voice is not self.primary:
            await self._voice.aclose()
        for made in self._made:
            await made.aclose()


def voices_needed(faults: Sequence[Stage]) -> bool:
    return Stage.TTS in faults
