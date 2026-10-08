from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from dafter_core.hashing import seal
from dafter_runtime.answering import Roster
from dafter_runtime.backchannel import Acknowledgements
from dafter_runtime.called import Called
from dafter_runtime.delivery import Delivery
from dafter_runtime.listeners import Listener, listener_session
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.toolbox import Answering, registry_for
from livekit.agents import AgentSession, JobContext, llm
from session_rig import Microphone, ScriptedSTT, ScriptedVAD, SlowReader, Speaker, until
from stub_llm import StubLLM

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
ASHA = "p_4b81e0d7"
RESUME_MS = 300
READ_S = 3.0
OPENING = "नमस्ते, मैं आपको आज की योजना के बारे में बताती हूँ।"
ECHO = ("मैं आपको आज", "मैं आपको आज की योजना")
MORE_ECHO = ("योजना के बारे", "योजना के बारे में बताती हूँ")
REPLY = "आपका ऑर्डर कल तक पहुँच जाएगा, मैं आपको उसकी जानकारी भेजती हूँ।"
REPLY_ECHO = ("आपका ऑर्डर कल", "आपका ऑर्डर कल तक पहुँच जाएगा")
CALLER = ("रुकिए", "रुकिए, एक सवाल है")
IN_HER_WORDS = ("आपका ऑर्डर", "आपका ऑर्डर कब आएगा")


@dataclass(frozen=True)
class Outcome:
    interrupted: bool
    turns: list[str]
    paused: int


def planned(mode: str, cancels_echo: bool) -> Plan:
    doc = json.loads(JOB.read_bytes())
    doc["turn"]["interruption"]["falseInterruptionTimeoutMs"] = RESUME_MS
    doc["agent"]["addressing"]["mode"] = mode
    doc["media"]["audio"]["echoCancellation"] = cancels_echo
    sealed, _ = seal(json.dumps(doc))
    return plan(load(sealed), "dafter-py")


async def hears(
    recognizer: ScriptedSTT, detector: ScriptedVAD, says: tuple[str, ...], speaker: Speaker
) -> None:
    played = speaker.played
    detector.talks(0.3)
    await until(lambda: speaker.paused or speaker.played > played + 0.4)
    recognizer.says(*says)
    detector.stops()
    await asyncio.sleep(0.2)
    await until(lambda: not speaker.paused)


def echoed_always(*heard: tuple[str, ...]) -> Outcome:
    p = planned("always", cancels_echo=False)

    async def run() -> Outcome:
        recognizer, detector, speaker = ScriptedSTT(), ScriptedVAD(), Speaker()
        reader = SlowReader(READ_S)
        session: AgentSession[Any] = AgentSession(
            stt=recognizer,
            llm=StubLLM(),
            tts=reader,
            vad=detector,
            turn_handling=p.turn_handling,  # type: ignore[arg-type]
            user_away_timeout=None,
            aec_warmup_duration=None,
        )
        session.input.audio = Microphone()
        session.output.audio = speaker
        delivery = Delivery(p.config.agent.speech, p.config.language, p.config.media.audio)
        registry = registry_for(p, session, Roster(), lambda: None, None)
        acknowledgements = Acknowledgements.of(p.config.turn.interruption.backchannel)
        agent = Answering(
            p.persona.instructions, registry, lambda: None, acknowledgements, delivery
        )
        await session.start(agent, record=False)
        delivery.start(session, reader)
        said = session.say(OPENING)
        await until(lambda: session.agent_state == "speaking")
        for says in heard:
            await hears(recognizer, detector, says, speaker)
        await asyncio.wait_for(asyncio.ensure_future(_done(said)), 10)
        await asyncio.sleep(0.6)
        turns = [
            m.text_content or ""
            for m in session.history.items
            if isinstance(m, llm.ChatMessage) and m.role == "user"
        ]
        outcome = Outcome(said.interrupted, turns, speaker.pauses)
        await session.aclose()
        return outcome

    return asyncio.run(run())


class Room:
    def __init__(self) -> None:
        self.remote_participants: dict[str, Any] = {}

    def on(self, *_: Any) -> None:
        return None


def echoed_called(
    *heard: tuple[str, ...], then: tuple[tuple[str, ...], ...] = (), cancels_echo: bool = False
) -> Outcome:
    p = planned("transcript", cancels_echo)

    async def run() -> Outcome:
        recognizer, detector, speaker = ScriptedSTT(), ScriptedVAD(), Speaker()
        reader = SlowReader(READ_S)
        voice_session: AgentSession[Any] = AgentSession(
            llm=StubLLM(REPLY),
            tts=reader,
            turn_handling=p.voice_turn_handling,  # type: ignore[arg-type]
            user_away_timeout=None,
            aec_warmup_duration=None,
        )
        voice_session.output.audio = speaker
        delivery = Delivery(p.config.agent.speech, p.config.language, p.config.media.audio)
        stages = SimpleNamespace(turn_detector=None, vad=detector, listener_stt=lambda: recognizer)
        ctx = SimpleNamespace(room=Room(), add_shutdown_callback=lambda _: None)
        job = cast(JobContext, ctx)
        called = Called(job, p, cast(Any, stages), voice_session, 16000, None, delivery)
        registry = registry_for(p, voice_session, called.roster, called.addressee, None)
        await voice_session.start(
            Answering(p.persona.instructions, registry, called.addressee, None, delivery),
            record=False,
        )
        delivery.start(voice_session, reader)
        called.listen()
        called.roster.join(ASHA)
        listening = listener_session(recognizer, detector, p.turn_handling)
        listening.input.audio = Microphone()
        called._listening(ASHA, listening)
        turns: list[str] = []

        def heard_by_gate(speaker: str, text: str, timing: llm.MetricsReport) -> None:
            turns.append(text)
            called._heard(speaker, text, timing)

        await listening.start(
            Listener(ASHA, heard_by_gate, called._hearing(ASHA, listening)), record=False
        )
        called.gate.wake(ASHA)
        reply = called.voice.reply
        assert reply is not None
        await until(lambda: voice_session.agent_state == "speaking")
        for says in heard:
            await hears(recognizer, detector, says, speaker)
        await asyncio.wait_for(asyncio.ensure_future(_done(reply)), 10)
        await asyncio.sleep(0.6)
        if then:
            called.gate.wake(ASHA)
            reply = called.voice.reply
            assert reply is not None
            await until(lambda: voice_session.agent_state == "speaking")
            for says in then:
                await hears(recognizer, detector, says, speaker)
            await asyncio.wait_for(asyncio.ensure_future(_done(reply)), 10)
            await asyncio.sleep(0.6)
        outcome = Outcome(reply.interrupted, turns, speaker.pauses)
        await listening.aclose()
        await voice_session.aclose()
        return outcome

    return asyncio.run(run())


async def _done(handle: Any) -> None:
    await handle


def test_the_agent_heard_back_on_a_line_without_echo_cancellation_is_not_a_turn() -> None:
    outcome = echoed_always(ECHO, MORE_ECHO)
    assert outcome == Outcome(interrupted=False, turns=[], paused=1)


def test_on_an_echoing_line_the_caller_still_cuts_the_agent_with_new_words() -> None:
    outcome = echoed_always(ECHO, CALLER)
    assert outcome.interrupted
    assert outcome.turns == [CALLER[-1]]


def test_a_called_agent_does_not_answer_or_cut_itself_from_its_echo() -> None:
    outcome = echoed_called(REPLY_ECHO, REPLY_ECHO)
    assert outcome == Outcome(interrupted=False, turns=[], paused=1)


def test_a_called_agent_on_an_echoing_line_is_still_cut_by_the_caller() -> None:
    outcome = echoed_called(REPLY_ECHO, CALLER)
    assert outcome.interrupted
    assert outcome.turns == [CALLER[-1]]


def test_on_an_echoing_line_the_caller_still_stops_the_agent_with_a_short_stop() -> None:
    outcome = echoed_called(REPLY_ECHO, ("रुको",))
    assert outcome.interrupted


def test_an_echo_on_one_reply_does_not_deafen_the_caller_on_the_next() -> None:
    outcome = echoed_called(REPLY_ECHO, then=(("सुनो", "सुनो ज़रा"),))
    assert outcome.interrupted


def test_where_echo_is_cancelled_a_caller_using_her_words_cuts_her_and_is_answered() -> None:
    outcome = echoed_called(IN_HER_WORDS, cancels_echo=True)
    assert outcome.interrupted
    assert outcome.turns == [IN_HER_WORDS[-1]]


def test_on_a_line_without_echo_cancellation_the_same_words_are_taken_for_her_echo() -> None:
    outcome = echoed_called(IN_HER_WORDS, cancels_echo=False)
    assert not outcome.interrupted
    assert outcome.turns == []
