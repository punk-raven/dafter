from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dafter_core.config import ProviderRef
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError
from livekit.agents import llm as lk_llm
from livekit.agents import stt as lk_stt
from livekit.agents import tts as lk_tts

from .plan import Plan


@dataclass(frozen=True, slots=True)
class Stages:
    stt: lk_stt.STT[Any]
    llm: lk_llm.LLM[Any]
    tts: lk_tts.TTS[Any]


def _ref(ref: ProviderRef | None, stage: Stage) -> ProviderRef:
    if ref is None:
        raise DafterError(ErrorCode.INTERNAL, f"a planned pipeline lost its {stage} stage")
    return ref


def build(plan: Plan) -> Stages:
    cfg = plan.config
    stt_ref = _ref(plan.pipeline.stt, Stage.STT)
    llm_ref = _ref(plan.pipeline.llm, Stage.LLM)
    tts_ref = _ref(plan.pipeline.tts, Stage.TTS)
    if plan.stt.stt is None or plan.llm.llm is None or plan.tts.tts is None:
        raise DafterError(ErrorCode.INTERNAL, "a planned vendor lost a stage factory")
    stages = Stages(
        stt=plan.stt.stt(stt_ref, cfg.language, cfg.turn),
        llm=plan.llm.llm(llm_ref),
        tts=plan.tts.tts(tts_ref, cfg.language),
    )
    if plan.tts.wants_prewarm(tts_ref):
        stages.tts.prewarm()
    return stages
