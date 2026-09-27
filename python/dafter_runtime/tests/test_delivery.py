from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from dafter_core.config import parse
from dafter_core.enums import Situation
from dafter_runtime.answering import Roster
from dafter_runtime.delivery import Delivery, Situational
from dafter_runtime.plan import plan
from dafter_runtime.toolbox import Answering, registry_for
from livekit.agents import AgentSession
from session_rig import SlowReader, Speaker, until
from stub_llm import StubLLM

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
CFG = parse(JOB.read_bytes())


@pytest.mark.parametrize(
    ("heard", "situation"),
    [
        ("मेरा इंटरनेट नहीं चल रहा है", Situation.CONCERN),
        ("नमस्ते, मुझे एक शिकायत करनी है", Situation.CONCERN),
        ("my order is broken", Situation.CONCERN),
        ("ನನಗೆ ಒಂದು ಸಮಸ್ಯೆ ಇದೆ", Situation.CONCERN),
        ("नमस्ते जी", Situation.GREETING),
        ("hello there", Situation.GREETING),
        ("నమస్కారం", Situation.GREETING),
        ("अभी समय क्या हुआ है?", Situation.NEUTRAL),
        ("[Asha, to you] निव्या, मेरा फ़ोन खराब है", Situation.CONCERN),
        (None, Situation.NEUTRAL),
    ],
)
def test_the_catalog_cues_pick_the_situation_of_the_callers_turn(
    heard: str | None, situation: Situation
) -> None:
    assert Situational(CFG.agent.speech.situations).of(heard) is situation


def test_the_opening_is_a_greeting_and_a_disabled_classifier_is_always_neutral() -> None:
    situations = CFG.agent.speech.situations
    assert Situational(situations).opening() is Situation.GREETING
    off = Situational(replace(situations, enabled=False))
    assert off.opening() is Situation.NEUTRAL
    assert off.of("मेरी समस्या सुनिए") is Situation.NEUTRAL


class StyledReader(SlowReader):
    def __init__(self, seconds: float) -> None:
        super().__init__(seconds)
        self.styles: list[str] = []

    def style(self, situation: str) -> None:
        self.styles.append(situation)


class Conversation:
    def __init__(self) -> None:
        self.reader = StyledReader(0.3)
        self.speaker = Speaker()
        self.session: AgentSession[Any] = AgentSession(llm=StubLLM(), tts=self.reader)
        self.session.output.audio = self.speaker
        self.delivery = Delivery(CFG.agent.speech)

    async def start(self) -> None:
        p = plan(CFG, "dafter-py")
        registry = registry_for(p, self.session, Roster(), lambda: None, None)
        agent = Answering(p.persona.instructions, registry, lambda: None, None, self.delivery)
        await self.session.start(agent, record=False)

    async def turn(self, said: str) -> None:
        await self.session.generate_reply(user_input=said)
        await until(lambda: self.session.agent_state == "listening")


def converse(*turns: str) -> Conversation:
    async def run() -> Conversation:
        conversation = Conversation()
        await conversation.start()
        await conversation.session.say("नमस्ते! बताइए।")
        for said in turns:
            await conversation.turn(said)
        await conversation.session.aclose()
        return conversation

    return asyncio.run(run())


def test_each_reply_is_voiced_for_the_situation_of_the_turn_it_answers() -> None:
    c = converse("मेरा इंटरनेट नहीं चल रहा", "अभी समय क्या है?", "नमस्ते")
    assert c.reader.styles == ["greeting", "concern", "neutral", "greeting"]
