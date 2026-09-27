from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from dafter_core.config import ProviderRef, Turn
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError, ProviderContext
from livekit.agents import llm as lk_llm
from livekit.agents import stt as lk_stt
from livekit.agents import tts as lk_tts
from livekit.agents import vad as lk_vad

from . import sarvam, silero
from .batch import BatchTranscriber


@dataclass(frozen=True, slots=True)
class Vendor:
    name: str
    languages: frozenset[str]
    native_endpointing: bool
    vad: Callable[[ProviderRef], lk_vad.VAD] | None
    stt: Callable[[ProviderRef, str, Turn, str | None], lk_stt.STT[Any]] | None
    llm: Callable[[ProviderRef], lk_llm.LLM[Any]] | None
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
            vad=silero.build_vad,
            stt=None,
            llm=None,
            tts=None,
            batch=None,
            wants_prewarm=lambda _: False,
            classify=silero.classify,
        ),
    }
)


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


def vendor_for(ref: ProviderRef | None, stage: Stage) -> Vendor:
    if ref is None:
        raise DafterError(
            ErrorCode.INVALID_CONFIG,
            f"the pipeline names no {stage} provider",
            stage=stage,
            details=(f"at '/agent/pipeline/{stage}': required for a cascaded agent",),
        )
    vendor = VENDORS.get(ref.provider)
    if vendor is None or getattr(vendor, str(stage)) is None:
        raise DafterError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"no {stage} provider named {ref.provider} is registered in this worker",
            stage=stage,
            provider=ProviderContext(ref.provider),
            details=(f"at '/agent/pipeline/{stage}/provider': registered: {', '.join(VENDORS)}",),
        )
    return vendor
