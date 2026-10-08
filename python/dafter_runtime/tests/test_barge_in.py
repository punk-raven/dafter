from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, cast

import pytest
from dafter_core.enums import WakeSource
from dafter_core.hashing import seal
from dafter_runtime.addressing import Gate, Said, Timing
from dafter_runtime.answering import Roster, Voice
from dafter_runtime.barge_in import BargeIn, follow
from dafter_runtime.called import barge_in_for
from dafter_runtime.naming import Matcher
from dafter_runtime.plan import load, plan
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    Agent,
    AgentSession,
    APIConnectOptions,
    TurnHandlingOptions,
    llm,
)
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.voice.events import UserInputTranscribedEvent, UserStateChangedEvent
from stub_llm import StubLLM, StubStream

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
ASHA = "p_4b81e0d7"
RAVI = "p_9d02c3aa"
MIN_S = 0.25


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Pending:
    def __init__(self, delay: float, callback: Callable[[], None]) -> None:
        self.delay = delay
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class Scheduler:
    def __init__(self) -> None:
        self.pending: list[Pending] = []

    def __call__(self, delay: float, callback: Callable[[], None]) -> Pending:
        self.pending.append(Pending(delay, callback))
        return self.pending[-1]

    def live(self) -> list[Pending]:
        return [t for t in self.pending if not t.cancelled]

    def fire(self) -> None:
        for timer in self.live():
            timer.cancelled = True
            timer.callback()


class Rig:
    def __init__(self, caller: str | None = ASHA, min_words: int = 0) -> None:
        self.caller = caller
        self.stops = 0
        self.clock = Clock()
        self.scheduler = Scheduler()
        self.barge_in = BargeIn(
            MIN_S, min_words, lambda: self.caller, self._stop, self.clock, self.scheduler
        )

    def _stop(self) -> None:
        self.stops += 1


def test_the_caller_talking_for_the_minimum_duration_stops_the_reply() -> None:
    rig = Rig()
    rig.clock.now = 1000.05
    rig.barge_in.speaking(ASHA, 1000.0)
    [held] = rig.scheduler.live()
    assert round(held.delay, 3) == 0.2
    assert rig.stops == 0
    rig.scheduler.fire()
    assert rig.stops == 1


def test_speech_shorter_than_the_minimum_does_not_stop_it() -> None:
    rig = Rig()
    rig.barge_in.speaking(ASHA, rig.clock.now)
    rig.barge_in.quiet(ASHA)
    assert rig.scheduler.live() == []
    assert rig.stops == 0


def test_only_the_person_who_woke_it_can_cut_it_off() -> None:
    rig = Rig()
    rig.barge_in.speaking(RAVI, rig.clock.now)
    rig.scheduler.fire()
    assert rig.stops == 0
    dormant = Rig(caller=None)
    dormant.barge_in.speaking(ASHA, dormant.clock.now)
    dormant.scheduler.fire()
    assert dormant.stops == 0


def test_a_caller_who_left_is_forgotten() -> None:
    rig = Rig()
    rig.barge_in.speaking(ASHA, rig.clock.now)
    rig.barge_in.left(ASHA)
    assert rig.scheduler.live() == []


def test_min_words_waits_for_enough_words_of_the_current_turn() -> None:
    rig = Rig(min_words=3)
    rig.barge_in.transcribed(ASHA, "एक दो", final=True)
    rig.barge_in.committed(ASHA)
    rig.barge_in.speaking(ASHA, rig.clock.now)
    rig.scheduler.fire()
    rig.barge_in.transcribed(ASHA, "रुको", final=True)
    rig.barge_in.transcribed(ASHA, "एक", final=False)
    assert rig.stops == 0
    rig.barge_in.transcribed(ASHA, "एक मिनट", final=False)
    assert rig.stops == 1


def test_a_reply_starting_while_the_caller_still_talks_is_stopped() -> None:
    rig = Rig()
    rig.barge_in.speaking(ASHA, rig.clock.now)
    rig.scheduler.fire()
    rig.barge_in.replying()
    assert rig.stops == 2
    rig.barge_in.quiet(ASHA)
    rig.barge_in.replying()
    assert rig.stops == 2


def test_a_listener_session_feeds_its_user_state_and_transcripts() -> None:
    rig = Rig(min_words=2)

    async def run() -> None:
        listener: AgentSession[None] = AgentSession()
        follow(rig.barge_in, ASHA, listener)
        listener.emit(
            "user_state_changed",
            UserStateChangedEvent(old_state="listening", new_state="speaking", created_at=1000.0),
        )
        rig.scheduler.fire()
        listener.emit(
            "user_input_transcribed",
            UserInputTranscribedEvent(transcript="रुको ज़रा", is_final=False),
        )
        listener.emit(
            "user_state_changed",
            UserStateChangedEvent(old_state="speaking", new_state="listening"),
        )
        await listener.aclose()

    asyncio.run(run())
    assert rig.stops == 1
    assert rig.scheduler.live() == []


class Responder:
    def __init__(self) -> None:
        self.stops = 0
        self.pauses = 0
        self.resumes = 0

    def answer(
        self,
        speaker: str,
        text: str,
        overheard: list[Said],
        timing: Timing | None,
        judged: bool = False,
    ) -> None:
        return None

    def hush(self) -> None:
        return None

    def addressed(self, woken_by: str | None, via: WakeSource | None) -> None:
        return None

    def barge_in(self) -> None:
        self.stops += 1

    def pause(self) -> bool:
        self.pauses += 1
        return True

    def resume(self) -> None:
        self.resumes += 1


class Loop:
    def __init__(self, scheduler: Scheduler) -> None:
        self.scheduler = scheduler

    def call_later(self, delay: float, callback: Callable[[], None]) -> Pending:
        return self.scheduler(delay, callback)


def test_a_called_session_pauses_on_stage_1s_minimum_and_stops_on_words_of_whoever_woke_it() -> (
    None
):
    doc = json.loads(JOB.read_bytes())
    doc["agent"]["addressing"]["mode"] = "transcript"
    sealed, _ = seal(json.dumps(doc))
    p = plan(load(sealed), "dafter-py")
    responder = Responder()
    scheduler = Scheduler()
    gate = Gate(
        Matcher.for_agent(p.config.agent),
        20.0,
        responder,
        clock=Clock(),
        schedule=scheduler,
    )
    loop = cast(asyncio.AbstractEventLoop, Loop(scheduler))
    barge_in = barge_in_for(p, gate, cast(Voice, responder), loop)
    gate.wake(RAVI)
    barge_in.speaking(ASHA, time.time())
    barge_in.speaking(RAVI, time.time())
    held = scheduler.pending[-2:]
    assert all(MIN_S - 0.05 < t.delay <= MIN_S for t in held)
    for timer in held:
        timer.callback()
    assert (responder.pauses, responder.stops) == (1, 0)
    barge_in.transcribed(ASHA, "रुको", final=False)
    assert responder.stops == 0
    barge_in.transcribed(RAVI, "रुको", final=False)
    assert responder.stops == 1


class HeldStream(StubStream):
    async def _run(self) -> None:
        assert isinstance(self._llm, HeldLLM)
        await self._llm.release.wait()
        await super()._run()


class HeldLLM(StubLLM):
    def __init__(self) -> None:
        super().__init__()
        self.release = asyncio.Event()

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
        return HeldStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


def on_voice(
    script: Callable[[AgentSession[None], HeldLLM], Awaitable[None]], interruptible: bool = True
) -> None:
    async def run() -> None:
        held = HeldLLM()
        handling: TurnHandlingOptions = {"interruption": {"enabled": interruptible}}
        async with AgentSession[None](llm=held, turn_handling=handling) as session:
            await session.start(Agent(instructions=""))
            await script(session, held)

    asyncio.run(run())


async def replying(session: AgentSession[None]) -> None:
    for _ in range(100):
        if session.current_speech is not None:
            return
        await asyncio.sleep(0)
    raise AssertionError("the reply never started")


def test_the_voice_reply_is_interrupted_when_the_caller_barges_in() -> None:
    async def script(session: AgentSession[None], held: HeldLLM) -> None:
        voice = Voice(session, Roster(), interruptible=True)
        voice.answer(ASHA, "निव्या, समय क्या है?", [])
        await replying(session)
        voice.barge_in()
        assert voice.reply is not None and voice.reply.interrupted
        held.release.set()
        await voice.reply

    on_voice(script)


def test_a_reply_that_disallows_interruptions_or_a_session_without_them_keeps_playing() -> None:
    async def script(session: AgentSession[None], held: HeldLLM) -> None:
        pinned = session.generate_reply(user_input="hello", allow_interruptions=False)
        await replying(session)
        Voice(session, Roster(), interruptible=True).barge_in()
        assert not pinned.interrupted
        held.release.set()
        await pinned
        held.release.clear()
        voice = Voice(session, Roster(), interruptible=False)
        voice.answer(ASHA, "hello", [])
        await replying(session)
        voice.barge_in()
        assert voice.reply is not None and not voice.reply.interrupted
        held.release.set()
        await voice.reply

    on_voice(script)


@pytest.mark.parametrize("interruptible", [True, False], ids=["interruptible", "uninterruptible"])
@pytest.mark.parametrize("stop", ["Nivya, stop", "निव्या, बस"], ids=["english", "hindi"])
def test_a_stop_command_ends_the_reply_and_sleeps_whatever_the_interruption_setting(
    interruptible: bool, stop: str
) -> None:
    doc = json.loads(JOB.read_bytes())
    doc["agent"]["addressing"]["mode"] = "transcript"
    sealed, _ = seal(json.dumps(doc))
    agent = plan(load(sealed), "dafter-py").config.agent

    async def script(session: AgentSession[None], held: HeldLLM) -> None:
        voice = Voice(session, Roster(), interruptible=interruptible)
        gate = Gate(
            Matcher.for_agent(agent),
            20.0,
            voice,
            clock=Clock(),
            schedule=Scheduler(),
            name=agent.name or "",
        )
        gate.heard(ASHA, "Nivya, what time is it?")
        await replying(session)
        gate.heard(ASHA, stop)
        assert gate.dormant
        assert voice.reply is not None and voice.reply.interrupted
        held.release.set()
        await voice.reply

    on_voice(script, interruptible)
