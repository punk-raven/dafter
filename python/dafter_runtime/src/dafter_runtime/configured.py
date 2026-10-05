from __future__ import annotations

from typing import Any

from dafter_core.speech import Backchannel
from livekit.agents import llm as lk_llm

from .backchannel import Acknowledgements
from .cost import model_name, provider_name
from .speech_plan import SpeechPlan


def configured(
    llm: lk_llm.LLM[Any], speech_plan: SpeechPlan, fillers: bool, backchannel: Backchannel
) -> dict[str, Any]:
    return {
        "llm": {"provider": provider_name(llm.provider), "model": model_name(llm.model)},
        "fillers": fillers,
        "backchannel": Acknowledgements.of(backchannel) is not None,
        "normalization": "platform" if speech_plan.normalizes else "provider",
    }
