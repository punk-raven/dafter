from __future__ import annotations

import logging
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from dafter_core.config import ProviderRef
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError
from dafter_providers import fallback
from livekit import rtc
from livekit.agents import llm as lk_llm
from livekit.agents import stt as lk_stt
from livekit.agents import tts as lk_tts
from livekit.agents import vad as lk_vad
from livekit.agents.voice.room_io import AudioInputOptions
from livekit.agents.voice.room_io.types import NoiseCancellationParams, NoiseCancellationSelector
from livekit.agents.voice.turn import _StreamingTurnDetector

from .metrics import Moment, counted
from .plan import FALLBACK_AT, Failover, Plan

log = logging.getLogger("dafter.runtime")


@dataclass(frozen=True, slots=True)
class Stages:
    stt: lk_stt.STT[Any]
    llm: lk_llm.LLM[Any]
    tts: lk_tts.TTS[Any]
    vad: lk_vad.VAD | None
    turn_detector: _StreamingTurnDetector | None
    listener_stt: Callable[[], lk_stt.STT[Any]]


def _ref(ref: ProviderRef | None, stage: Stage) -> ProviderRef:
    if ref is None:
        raise DafterError(ErrorCode.INTERNAL, f"a planned pipeline lost its {stage} stage")
    return ref


def _vad(plan: Plan) -> lk_vad.VAD | None:
    if plan.vad is None:
        return None
    if plan.vad.vad is None:
        raise DafterError(ErrorCode.INTERNAL, "a planned vendor lost a stage factory")
    return plan.vad.vad(_ref(plan.pipeline.vad, Stage.VAD))


def _built(plan: Plan, stage: Stage, failover: Failover, at: str) -> Any:
    vendor, ref = failover.vendor, failover.ref
    if stage is Stage.LLM and vendor.llm is not None:
        made: Any = vendor.llm(ref, at)
    elif stage is Stage.TTS and vendor.tts is not None:
        made = vendor.tts(ref, plan.config.language)
    else:
        raise DafterError(ErrorCode.INTERNAL, f"a planned fallback lost its {stage} factory")
    if vendor.wants_prewarm(ref):
        made.prewarm()
    return made


def _fallbacks(plan: Plan, stage: Stage) -> tuple[list[Any], list[fallback.Classify]]:
    made: list[Any] = []
    classifiers: list[fallback.Classify] = []
    for i, failover in enumerate(plan.fallbacks.get(stage, ())):
        at = f"{FALLBACK_AT}/{stage}/{i}"
        try:
            made.append(_built(plan, stage, failover, at))
        except DafterError as exc:
            log.error(
                "fallback provider not built",
                extra={"stage": str(stage), "at": at, "code": str(exc.code)},
            )
            continue
        classifiers.append(failover.vendor.classify)
    return made, classifiers


def _counting_switches(stage: Stage, built: Any, primary: Any) -> Any:
    if built is not primary:
        fallback.follow_switches(built, stage, lambda: counted(Moment.PROVIDER_SWITCHED))
    return built


def build(plan: Plan, faults: Collection[Stage] | None = None) -> Stages:
    faulted = fallback.injected_faults() if faults is None else frozenset(faults)
    cfg = plan.config
    stt_ref = _ref(plan.pipeline.stt, Stage.STT)
    llm_ref = _ref(plan.pipeline.llm, Stage.LLM)
    tts_ref = _ref(plan.pipeline.tts, Stage.TTS)
    new_stt = plan.stt.stt
    if new_stt is None or plan.llm.llm is None or plan.tts.tts is None:
        raise DafterError(ErrorCode.INTERNAL, "a planned vendor lost a stage factory")

    def listener_stt() -> lk_stt.STT[Any]:
        return new_stt(stt_ref, plan.hearing, cfg.turn, plan.stt_prompt)

    primary_llm = plan.llm.llm(llm_ref)
    if plan.llm.wants_prewarm(llm_ref):
        primary_llm.prewarm()
    primary_tts = plan.tts.tts(tts_ref, cfg.language)
    if plan.tts.wants_prewarm(tts_ref):
        primary_tts.prewarm()
    llms, llm_classifiers = _fallbacks(plan, Stage.LLM)
    ttss, tts_classifiers = _fallbacks(plan, Stage.TTS)
    llm = fallback.llm(
        primary_llm, llms, Stage.LLM in faulted, [plan.llm.classify, *llm_classifiers]
    )
    tts = fallback.tts(
        primary_tts, ttss, Stage.TTS in faulted, [plan.tts.classify, *tts_classifiers]
    )
    return Stages(
        stt=listener_stt(),
        llm=_counting_switches(Stage.LLM, llm, primary_llm),
        tts=_counting_switches(Stage.TTS, tts, primary_tts),
        vad=_vad(plan),
        turn_detector=plan.turn_detector.build() if plan.turn_detector is not None else None,
        listener_stt=listener_stt,
    )


def hearing(plan: Plan, stages: Stages) -> dict[str, Any]:
    handling = dict(plan.turn_handling)
    if stages.turn_detector is not None:
        handling["turn_detection"] = stages.turn_detector
    return handling


def filtered(plan: Plan) -> NoiseCancellationSelector | None:
    chosen = plan.noise_filter
    if chosen is None:
        return None
    wide = chosen.build(chosen.name)
    narrow = chosen.build(chosen.phone_variant)

    def through(params: NoiseCancellationParams) -> rtc.NoiseCancellationOptions:
        if params.participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP:
            return narrow
        return wide

    return through


def filtered_input(plan: Plan, sample_rate: int) -> AudioInputOptions:
    return AudioInputOptions(sample_rate=sample_rate, noise_cancellation=filtered(plan))
