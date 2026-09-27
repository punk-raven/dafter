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
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, AgentSession, APIConnectOptions, llm
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.voice.events import ConversationItemAddedEvent
from session_rig import SlowReader, Speaker, until
from stub_llm import StubLLM, StubStream

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


class LateStream(StubStream):
    async def _run(self) -> None:
        assert isinstance(self._llm, LateLLM)
        await asyncio.sleep(self._llm.late)
        await super()._run()


class LateLLM(StubLLM):
    def __init__(self, late: float) -> None:
        super().__init__("ठीक है, मैं देखता हूँ।")
        self.late = late

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
        return LateStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


def speech_config(after_ms: int) -> Any:
    fillers = replace(CFG.agent.speech.fillers, after_ms=after_ms)
    return replace(CFG.agent.speech, fillers=fillers)


class Conversation:
    def __init__(self, late: float, after_ms: int = 150) -> None:
        self.reader = StyledReader(0.3)
        self.speaker = Speaker()
        self.session: AgentSession[Any] = AgentSession(llm=LateLLM(late), tts=self.reader)
        self.session.output.audio = self.speaker
        self.delivery = Delivery(speech_config(after_ms), "hi")
        self.filled: list[bool] = []
        self.session.on("conversation_item_added", self._added)

    def _added(self, ev: ConversationItemAddedEvent) -> None:
        if isinstance(ev.item, llm.ChatMessage) and ev.item.role == "assistant":
            self.filled.append(self.delivery.filler.took(self.session.current_speech))

    async def start(self) -> None:
        p = plan(CFG, "dafter-py")
        registry = registry_for(p, self.session, Roster(), lambda: None, None, self.delivery)
        agent = Answering(p.persona.instructions, registry, lambda: None, None, self.delivery)
        await self.session.start(agent, record=False)
        self.delivery.filler.start(self.session, self.reader)
        await until(lambda: len(self.reader.read) >= 3)

    async def turn(self, said: str) -> None:
        await self.session.generate_reply(user_input=said)
        await until(lambda: self.session.agent_state == "listening")


def converse(late: float, *turns: str) -> Conversation:
    async def run() -> Conversation:
        conversation = Conversation(late)
        await conversation.start()
        await conversation.session.say("नमस्ते! बताइए।")
        for said in turns:
            await conversation.turn(said)
        await conversation.session.aclose()
        return conversation

    return asyncio.run(run())


def test_each_reply_is_voiced_for_the_situation_of_the_turn_it_answers() -> None:
    c = converse(0.0, "मेरा इंटरनेट नहीं चल रहा", "अभी समय क्या है?", "नमस्ते")
    assert c.reader.styles == ["greeting", "concern", "neutral", "greeting"]


def test_a_slow_reply_plays_one_filler_ahead_of_it_and_says_so() -> None:
    c = converse(0.6, "मेरा बिल देखिए")
    fillers = CFG.agent.speech.fillers.phrases["hi"]
    assert c.reader.read[:3] == list(fillers)
    assert c.filled == [False, True]


def test_a_quick_reply_plays_no_filler() -> None:
    c = converse(0.0, "मेरा बिल देखिए")
    assert c.filled == [False, False]
