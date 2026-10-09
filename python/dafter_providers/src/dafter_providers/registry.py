from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Protocol

from dafter_core.config import DEFAULT_TURN_DETECTOR, ProviderRef, Turn
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError, ProviderContext
from dafter_core.pipeline import NO_NOISE_FILTER
from livekit import rtc
from livekit.agents import inference
from livekit.agents import llm as lk_llm
from livekit.agents import stt as lk_stt
from livekit.agents import tts as lk_tts
from livekit.agents import vad as lk_vad
from livekit.agents.inference.eot.languages import LOCAL_LANGUAGES
from livekit.agents.voice.turn import _StreamingTurnDetector

from . import krisp, openai_compat, sarvam, silero, smart_turn
from .batch import BatchTranscriber

AGENT_LLM = "/agent/pipeline/llm"


class BuildLLM(Protocol):
    def __call__(self, ref: ProviderRef, at: str = AGENT_LLM) -> lk_llm.LLM[Any]: ...


@dataclass(frozen=True, slots=True)
class Vendor:
    name: str
    languages: frozenset[str]
    native_endpointing: bool
    detects_language: bool
    vad: Callable[[ProviderRef], lk_vad.VAD] | None
    stt: Callable[[ProviderRef, str | None, Turn, str | None], lk_stt.STT[Any]] | None
    llm: BuildLLM | None
    tts: Callable[[ProviderRef, str], lk_tts.TTS[Any]] | None
    batch: Callable[[ProviderRef], BatchTranscriber] | None
    wants_prewarm: Callable[[ProviderRef], bool]
    classify: Callable[[BaseException, Stage], DafterError]


VENDORS: Mapping[str, Vendor] = MappingProxyType(
    {
        sarvam.NAME: Vendor(
            name=sarvam.NAME,
            languages=frozenset(sarvam.LANGUAGES),
            native_endpointing=True,
            detects_language=True,
            vad=None,
            stt=sarvam.build_stt,
            llm=sarvam.build_llm,
            tts=sarvam.build_tts,
            batch=sarvam.build_batch,
            wants_prewarm=sarvam.wants_prewarm,
            classify=sarvam.classify,
        ),
        silero.NAME: Vendor(
            name=silero.NAME,
            languages=frozenset(),
            native_endpointing=False,
            detects_language=False,
            vad=silero.build_vad,
            stt=None,
            llm=None,
            tts=None,
            batch=None,
            wants_prewarm=lambda _: False,
            classify=silero.classify,
        ),
        **{
            name: Vendor(
                name=name,
                languages=openai_compat.LANGUAGES,
                native_endpointing=False,
                detects_language=False,
                vad=None,
                stt=None,
                llm=openai_compat.builder(endpoint),
                tts=None,
                batch=None,
                wants_prewarm=openai_compat.wants_prewarm,
                classify=openai_compat.classifier(endpoint),
            )
            for name, endpoint in openai_compat.ENDPOINTS.items()
        },
    }
)


@dataclass(frozen=True, slots=True)
class NoiseFilter:
    name: str
    vendor: str
    narrowband: bool
    phone_variant: str
    installed: Callable[[], bool]
    build: Callable[[str], rtc.NoiseCancellationOptions]


NOISE_FILTERS: Mapping[str, NoiseFilter] = MappingProxyType(
    {
        name: NoiseFilter(
            name=name,
            vendor=krisp.NAME,
            narrowband=name not in krisp.WIDEBAND_ONLY,
            phone_variant=krisp.PHONE_VARIANTS[name],
            installed=krisp.installed,
            build=krisp.build_filter,
        )
        for name in krisp.MODELS
    }
)


def noise_filter_for(name: str) -> NoiseFilter | None:
    if name == NO_NOISE_FILTER:
        return None
    chosen = NOISE_FILTERS.get(name)
    if chosen is None:
        raise DafterError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "no noise filter of that name is registered in this worker",
            details=(f"at '{krisp.FILTER_AT}': registered: {', '.join(NOISE_FILTERS)}",),
        )
    return chosen


LIVEKIT_TURN_DETECTOR_VERSION: inference.TurnDetectorVersions = "v1-mini"


@dataclass(frozen=True, slots=True)
class TurnDetectorKind:
    name: str
    languages: frozenset[str]
    missing: Callable[[], str | None]
    build: Callable[[], _StreamingTurnDetector]
    prewarm: Callable[[], None]


def _livekit_detector() -> _StreamingTurnDetector:
    return inference.TurnDetector(version=LIVEKIT_TURN_DETECTOR_VERSION)


TURN_DETECTORS: Mapping[str, TurnDetectorKind] = MappingProxyType(
    {
        DEFAULT_TURN_DETECTOR: TurnDetectorKind(
            name=DEFAULT_TURN_DETECTOR,
            languages=frozenset(LOCAL_LANGUAGES),
            missing=lambda: None,
            build=_livekit_detector,
            prewarm=lambda: None,
        ),
        smart_turn.NAME: TurnDetectorKind(
            name=smart_turn.NAME,
            languages=smart_turn.LANGUAGES,
            missing=smart_turn.missing,
            build=smart_turn.build_detector,
            prewarm=smart_turn.prewarm,
        ),
    }
)


def turn_detector_for(name: str) -> TurnDetectorKind:
    chosen = TURN_DETECTORS.get(name)
    if chosen is None:
        raise DafterError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "no turn detector of that name is registered in this worker",
            details=(f"at '{smart_turn.DETECTOR_AT}': registered: {', '.join(TURN_DETECTORS)}",),
        )
    return chosen


def prewarm_turn_detectors() -> None:
    for kind in TURN_DETECTORS.values():
        kind.prewarm()


def batch_for(ref: ProviderRef | None) -> BatchTranscriber:
    if ref is None:
        raise DafterError(
            ErrorCode.INVALID_CONFIG,
            "the session pins no batch provider",
            stage=Stage.STT,
            details=("at '/transcription/batch': required for the transcript after the call",),
        )
    vendor = VENDORS.get(ref.provider)
    if vendor is None or vendor.batch is None:
        raise DafterError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"no batch provider named {ref.provider} is registered",
            stage=Stage.STT,
            provider=ProviderContext(ref.provider),
            details=(
                "at '/transcription/batch/provider': registered: "
                + ", ".join(n for n, v in VENDORS.items() if v.batch is not None),
            ),
        )
    return vendor.batch(ref)


def vendor_for(ref: ProviderRef | None, stage: Stage, at: str | None = None) -> Vendor:
    base = at or f"/agent/pipeline/{stage}"
    if ref is None:
        raise DafterError(
            ErrorCode.INVALID_CONFIG,
            f"the session names no {stage} provider",
            stage=stage,
            details=(f"at '{base}': required",),
        )
    vendor = VENDORS.get(ref.provider)
    if vendor is None or getattr(vendor, str(stage)) is None:
        raise DafterError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"no {stage} provider named {ref.provider} is registered in this worker",
            stage=stage,
            provider=ProviderContext(ref.provider),
            details=(f"at '{base}/provider': registered: {', '.join(VENDORS)}",),
        )
    return vendor
