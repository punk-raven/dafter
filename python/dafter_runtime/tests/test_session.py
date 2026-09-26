from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from dafter_core.hashing import seal
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.stages import build
from dafter_runtime.worker import new_session
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    AgentSession,
    APIConnectOptions,
    inference,
    llm,
)
from livekit.agents.types import NOT_GIVEN, NotGivenOr

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"


class OfflineLLM(llm.LLM[Any]):
    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> llm.LLMStream:
        raise AssertionError("a wiring test never generates")


@pytest.fixture(autouse=True)
def sarvam_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARVAM_API_KEY", "test-only-not-a-key")


def offline_plan(**turn: Any) -> Plan:
    doc = json.loads(JOB.read_bytes())
    doc["agent"]["pipeline"]["tts"]["options"]["prewarm"] = False
    doc["turn"].update(turn)
    sealed, _ = seal(json.dumps(doc))
    return plan(load(sealed), "dafter-py")


def check_session(p: Plan, check: Callable[[AgentSession[Any]], None]) -> None:
    async def run() -> None:
        built = build(p)
        await built.llm.aclose()
        stages = replace(built, llm=OfflineLLM())
        session = new_session(p, stages)
        try:
            check(session)
        finally:
            await session.aclose()
            await stages.tts.aclose()
            await stages.stt.aclose()

    asyncio.run(run())


def test_the_session_catches_barge_in_on_the_local_vad_and_ends_turns_on_the_recognizer() -> None:
    def check(session: AgentSession[Any]) -> None:
        assert isinstance(session.vad, inference.VAD)
        assert session.turn_detection == "stt"
        assert session.options.interruption["mode"] == "vad"
        assert session.options.interruption["min_duration"] == 0.25
        assert session.options.interruption["min_words"] == 0
        assert session.options.endpointing["mode"] == "fixed"

    check_session(offline_plan(), check)


def test_a_semantic_session_runs_the_on_device_detector_with_dynamic_endpointing() -> None:
    def check(session: AgentSession[Any]) -> None:
        detector = session.turn_detection
        assert isinstance(detector, inference.TurnDetector)
        assert detector.model == "turn-detector-v1-mini"
        assert isinstance(session.vad, inference.VAD)
        assert session.options.endpointing["mode"] == "dynamic"
        assert session.options.endpointing["max_delay"] == 2.5

    check_session(offline_plan(strategy="semantic", localVadEnabled=True), check)
