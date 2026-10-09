from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from dafter_core.config import ResolvedSessionConfig
from dafter_core.enums import EventType, SpeechNormalization
from dafter_providers import VENDORS
from dafter_runtime.configured import configured
from dafter_runtime.delivery import Filler
from dafter_runtime.events import SessionEvents
from dafter_runtime.plan import load
from dafter_runtime.speech_plan import SpeechPlan
from livekit.agents import llm as lk_llm

JOBS = Path(__file__).resolve().parents[3] / "testdata" / "agent"


@pytest.fixture(autouse=True)
def provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SARVAM_API_KEY", "GROQ_API_KEY"):
        monkeypatch.setenv(name, "test-only-not-a-key")


def job(name: str) -> ResolvedSessionConfig:
    return load((JOBS / name).read_bytes().strip())


def built_llm(cfg: ResolvedSessionConfig) -> lk_llm.LLM[Any]:
    ref = cfg.agent.pipeline.llm if cfg.agent.pipeline else None
    assert ref is not None
    build = VENDORS[ref.provider].llm
    assert build is not None
    return build(ref)


def effective(cfg: ResolvedSessionConfig) -> dict[str, Any]:
    speech = cfg.agent.speech
    return configured(
        built_llm(cfg),
        SpeechPlan(speech, cfg.language),
        Filler(speech.fillers, cfg.language).enabled,
        cfg.turn.interruption.backchannel,
    )


def test_the_catalog_hindi_session_runs_every_speech_setting() -> None:
    assert effective(job("hindi-groq-webrtc-job.json")) == {
        "llm": {"provider": "groq", "model": "qwen/qwen3.8-27b"},
        "fillers": True,
        "backchannel": True,
        "normalization": "platform",
    }


def test_a_language_without_platform_rules_is_reported_as_the_providers() -> None:
    cfg = dataclasses.replace(job("english-webrtc-job.json"), language="ta-IN")
    assert cfg.agent.speech.normalization is SpeechNormalization.PLATFORM
    assert effective(cfg)["normalization"] == "provider"
    assert effective(cfg)["llm"] == {"provider": "sarvam", "model": "sarvam-105b"}


def test_each_setting_the_session_turns_off_is_reported_off() -> None:
    cfg = job("hindi-webrtc-job.json")
    speech = dataclasses.replace(
        cfg.agent.speech,
        normalization=SpeechNormalization.PROVIDER,
        fillers=dataclasses.replace(cfg.agent.speech.fillers, enabled=False),
    )
    interruption = dataclasses.replace(
        cfg.turn.interruption,
        backchannel=dataclasses.replace(cfg.turn.interruption.backchannel, enabled=False),
    )
    off = dataclasses.replace(
        cfg,
        agent=dataclasses.replace(cfg.agent, speech=speech),
        turn=dataclasses.replace(cfg.turn, interruption=interruption),
    )
    assert effective(off) == {
        "llm": {"provider": "sarvam", "model": "sarvam-105b"},
        "fillers": False,
        "backchannel": False,
        "normalization": "provider",
    }


def test_the_payload_is_a_valid_agent_configured_event() -> None:
    cfg = job("hindi-groq-webrtc-job.json")

    async def publish(body: bytes) -> None:
        return None

    event = SessionEvents(cfg, publish).envelope(EventType.AGENT_CONFIGURED, effective(cfg))
    assert event.type is EventType.AGENT_CONFIGURED
