from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from dafter_core.hashing import seal
from dafter_providers.sarvam.sentences import SentenceTTS
from dafter_runtime.called import listening
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.stages import Stages, build
from dafter_runtime.worker import new_session
from livekit.agents import AgentSession, inference

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"


@pytest.fixture(autouse=True)
def sarvam_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARVAM_API_KEY", "test-only-not-a-key")


def called_plan(**turn: Any) -> Plan:
    doc = json.loads(JOB.read_bytes())
    doc["agent"]["addressing"]["mode"] = "transcript"
    doc["agent"]["pipeline"]["tts"]["options"]["prewarm"] = False
    doc["agent"]["pipeline"]["llm"]["options"]["prewarm"] = False
    doc["turn"].update(turn)
    sealed, _ = seal(json.dumps(doc))
    return plan(load(sealed), "dafter-py")


SEMANTIC = {"strategy": "semantic", "localVadEnabled": True}


def with_stages(p: Plan, check: Callable[[Stages], list[AgentSession[Any]]]) -> None:
    async def run() -> None:
        stages = build(p)
        sessions = check(stages)
        for session in sessions:
            await session.aclose()
            if session.stt is not None:
                await session.stt.aclose()
        await stages.stt.aclose()
        await stages.llm.aclose()
        await stages.tts.aclose()

    asyncio.run(run())


def test_every_listener_shares_the_one_vad_and_turn_detector_and_has_its_own_recognizer() -> None:
    def check(stages: Stages) -> list[AgentSession[Any]]:
        new = listening(called_plan(**SEMANTIC), stages)
        asha, ravi = new(), new()
        assert isinstance(stages.vad, inference.VAD)
        assert isinstance(stages.turn_detector, inference.TurnDetector)
        assert asha.vad is stages.vad and ravi.vad is stages.vad
        assert asha.turn_detection is stages.turn_detector
        assert ravi.turn_detection is stages.turn_detector
        assert asha.stt is not None and asha.stt is not ravi.stt
        assert asha.options.endpointing["mode"] == "dynamic"
        return [asha, ravi]

    with_stages(called_plan(**SEMANTIC), check)


def test_a_listener_ends_turns_on_the_recognizer_and_keeps_the_vad_for_barge_in() -> None:
    def check(stages: Stages) -> list[AgentSession[Any]]:
        listener = listening(called_plan(), stages)()
        assert listener.turn_detection == "stt"
        assert listener.vad is stages.vad
        interruption = listener.options.interruption
        assert (interruption["mode"], interruption["min_duration"]) == ("vad", 0.25)
        assert listener.options.preemptive_generation["enabled"] is False
        return [listener]

    with_stages(called_plan(), check)


def test_the_voice_session_generates_preemptively_and_speaks_sentence_by_sentence() -> None:
    def check(stages: Stages) -> list[AgentSession[Any]]:
        voice = new_session(called_plan(**SEMANTIC), stages)
        assert voice.stt is None and voice.vad is None
        assert voice.turn_detection == "manual"
        assert isinstance(voice.tts, SentenceTTS)
        assert voice.options.preemptive_generation["enabled"] is True
        assert voice.options.preemptive_generation["preemptive_tts"] is True
        return [voice]

    with_stages(called_plan(**SEMANTIC), check)


def test_listeners_joining_load_no_further_vad(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[inference.VAD] = []

    class Counted(inference.VAD):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            built.append(self)

    monkeypatch.setattr(inference, "VAD", Counted)

    def check(stages: Stages) -> list[AgentSession[Any]]:
        new = listening(called_plan(), stages)
        sessions = [new() for _ in range(3)]
        assert built == [stages.vad]
        return sessions

    with_stages(called_plan(), check)
