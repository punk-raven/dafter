from __future__ import annotations

import json
from typing import Any

import pytest
from dafter_core.config import parse
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError
from dafter_core.pipeline import Fallback

MINIMAL: dict[str, Any] = {
    "apiVersion": "dafter.dev/v1",
    "sessionId": "s_7f3a9c21",
    "tenantId": "t_9c21a4be",
    "privacyMode": "open",
    "language": "en-IN",
    "channel": "webrtc",
    "agent": {"enabled": True, "pool": "dafter-py"},
    "turn": {"strategy": "auto"},
    "recording": {"enabled": False},
    "budgets": {"turnGapP50Ms": 800, "turnGapP95Ms": 1500},
}
GROQ = {
    "provider": "groq",
    "model": "qwen/qwen3.8-27b",
    "credentialRef": "secret://tenants/t_9c21a4be/groq/api-key",
}


def doc(fallback: dict[str, Any] | None) -> str:
    d = json.loads(json.dumps(MINIMAL))
    d["agent"]["pipeline"] = {"llm": GROQ} if fallback is None else {"fallback": fallback}
    return json.dumps(d)


def test_a_pipeline_without_fallbacks_has_every_slot_off() -> None:
    pipeline = parse(doc(None)).agent.pipeline
    assert pipeline is not None
    assert pipeline.fallback == Fallback()


def test_a_pipeline_names_its_llm_fallback_and_an_empty_voice_slot() -> None:
    pipeline = parse(doc({"llm": [GROQ], "tts": []})).agent.pipeline
    assert pipeline is not None
    [route] = pipeline.fallback.llm
    assert (route.provider, route.model) == ("groq", "qwen/qwen3.8-27b")
    assert route.credential_ref == GROQ["credentialRef"]
    assert pipeline.fallback.tts == ()


@pytest.mark.parametrize(
    "fallback",
    [{"stt": [GROQ]}, {"llm": [GROQ, GROQ, GROQ]}],
    ids=["stt has no fallback", "three llm fallbacks"],
)
def test_a_fallback_the_schema_does_not_allow_is_refused(fallback: dict[str, Any]) -> None:
    with pytest.raises(DafterError) as exc:
        parse(doc(fallback))
    assert exc.value.code is ErrorCode.INVALID_CONFIG
    assert any("/agent/pipeline/fallback" in d for d in exc.value.details), exc.value.details
