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

from . import sarvam


@dataclass(frozen=True, slots=True)
class Vendor:
    name: str
    languages: frozenset[str]
    native_endpointing: bool
    vad: Callable[[ProviderRef], lk_vad.VAD] | None
    stt: Callable[[ProviderRef, str, Turn], lk_stt.STT[Any]] | None
    llm: Callable[[ProviderRef], lk_llm.LLM[Any]] | None
    tts: Callable[[ProviderRef, str], lk_tts.TTS[Any]] | None
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
            wants_prewarm=sarvam.wants_prewarm,
            classify=sarvam.classify,
        ),
    }
)


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
