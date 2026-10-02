from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dafter_core.hashing import seal
from dafter_runtime.answering import Roster, Voice
from dafter_runtime.backchannel import Acknowledgements, SessionFloor, acknowledged
from dafter_runtime.barge_in import BargeIn, follow
from dafter_runtime.called import resume_for
from dafter_runtime.listeners import Listener, listener_session
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.toolbox import Answering, registry_for
from livekit.agents import Agent, AgentSession, llm
from livekit.agents.voice import SpeechHandle
from session_rig import Microphone, ScriptedSTT, ScriptedVAD, SlowReader, Speaker, until
from stub_llm import StubLLM

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
ASHA = "p_4b81e0d7"
RESUME_MS = 300
READ_S = 1.5
OPENING = "नमस्ते, मैं आपको आज की योजना के बारे में बताती हूँ।"
QUESTION = "क्या मैं आपका ऑर्डर कैंसिल कर दूँ?"
PLAN_THEN_QUESTION = f"{OPENING} {QUESTION}"
TAIL = READ_S - 0.4


@dataclass(frozen=True)
class Outcome:
    interrupted: bool
    turns: list[str]
    paused: int


@dataclass(frozen=True)
class Caller:
    says: tuple[str, ...]
    after: float = 0.0
    again: tuple[str, ...] = ()


def planned(acknowledgements: bool = True, mode: str = "always") -> Plan:
    doc = json.loads(JOB.read_bytes())
    doc["turn"]["interruption"]["falseInterruptionTimeoutMs"] = RESUME_MS
    doc["turn"]["interruption"]["backchannel"]["enabled"] = acknowledgements
    doc["agent"]["addressing"]["mode"] = mode
    sealed, _ = seal(json.dumps(doc))
    return plan(load(sealed), "dafter-py")


async def talk(
    caller: Caller,
    opening: SpeechHandle,
    speaker: Speaker,
    recognizer: ScriptedSTT,
    detector: ScriptedVAD,
) -> None:
    await until(lambda: speaker.played >= caller.after)
    detector.talks(0.3)
    await until(lambda: speaker.paused)
    recognizer.says(*caller.says)
    detector.stops()
    if caller.again:
        await asyncio.sleep(0.1)
        detector.talks(0.3)
        recognizer.says(*caller.again)
        detector.stops()
    await asyncio.wait_for(_done(opening), 10)
    await asyncio.sleep(0.6)


def talked_over(
    caller_says: tuple[str, ...],
    acknowledgements: bool = True,
    opening: str = OPENING,
    caller: Caller | None = None,
    read: float = READ_S,
) -> Outcome:
    p = planned(acknowledgements)
    script = caller or Caller(caller_says)

    async def run() -> Outcome:
        recognizer, detector, speaker = ScriptedSTT(), ScriptedVAD(), Speaker()
        session: AgentSession[Any] = AgentSession(
            stt=recognizer,
            llm=StubLLM(),
            tts=SlowReader(read),
            vad=detector,
            turn_handling=p.turn_handling,  # type: ignore[arg-type]
            user_away_timeout=None,
            aec_warmup_duration=None,
        )
        session.input.audio = Microphone()
        session.output.audio = speaker
        registry = registry_for(p, session, Roster(), lambda: None, None)
        heard = Acknowledgements.of(p.config.turn.interruption.backchannel)
        agent = Answering(p.persona.instructions, registry, lambda: None, heard)
        await session.start(agent, record=False)
        said = session.say(opening)
        await until(lambda: session.agent_state == "speaking")
        await talk(script, said, speaker, recognizer, detector)
        turns = [
            m.text_content or ""
            for m in session.history.items
            if isinstance(m, llm.ChatMessage) and m.role == "user"
        ]
        outcome = Outcome(said.interrupted, turns, speaker.pauses)
        await session.aclose()
        return outcome

    return asyncio.run(run())


def called_over(caller: Caller, opening: str, read: float = READ_S) -> Outcome:
    p = planned(mode="transcript")
    interruption = p.config.turn.interruption

    async def run() -> Outcome:
        recognizer, detector, speaker = ScriptedSTT(), ScriptedVAD(), Speaker()
        voice_session: AgentSession[Any] = AgentSession(
            llm=StubLLM(),
            tts=SlowReader(read),
            turn_handling=p.voice_turn_handling,  # type: ignore[arg-type]
            user_away_timeout=None,
            aec_warmup_duration=None,
        )
        voice_session.output.audio = speaker
        await voice_session.start(Agent(instructions=""), record=False)
        roster = Roster()
        roster.join(ASHA)
        voice = Voice(voice_session, roster, interruptible=True)
        loop = asyncio.get_running_loop()
        barge_in = BargeIn(
            interruption.min_duration_ms / 1000,
            interruption.min_words,
            caller=lambda: ASHA,
            stop=voice.barge_in,
            clock=time.time,
            schedule=loop.call_later,
            resume=resume_for(p, voice),
            waits_for_words=True,
        )
        heard: list[str] = []
        listening = listener_session(recognizer, detector, p.turn_handling)
        listening.input.audio = Microphone()
        follow(barge_in, ASHA, listening)
        hearing = acknowledged(
            Acknowledgements.of(interruption.backchannel),
            SessionFloor(voice_session),
            lambda: barge_in.acknowledged(ASHA),
        )
        await listening.start(
            Listener(ASHA, lambda speaker, text, timing: heard.append(text), hearing),
            record=False,
        )
        said = voice_session.say(opening)
        await until(lambda: voice_session.agent_state == "speaking")
        await talk(caller, said, speaker, recognizer, detector)
        outcome = Outcome(said.interrupted, heard, speaker.pauses)
        await listening.aclose()
        await voice_session.aclose()
        return outcome

    return asyncio.run(run())


async def _done(handle: Any) -> None:
    await handle


def test_an_acknowledgement_pauses_the_agent_then_it_carries_on_and_no_turn_is_taken() -> None:
    outcome = talked_over(("हाँ", "हाँ जी"))
    assert outcome == Outcome(interrupted=False, turns=[], paused=1)


def test_real_words_over_the_agent_still_cut_it_and_become_the_turn() -> None:
    outcome = talked_over(("रुकिए", "रुकिए, एक सवाल है"))
    assert outcome.interrupted
    assert outcome.turns == ["रुकिए, एक सवाल है"]


def test_without_the_list_an_acknowledgement_cuts_the_agent_as_stage_1_did() -> None:
    outcome = talked_over(("हाँ", "हाँ जी"), acknowledgements=False)
    assert outcome.interrupted
    assert outcome.turns == ["हाँ जी"]


def test_an_acknowledgement_over_the_end_of_a_question_is_the_answer() -> None:
    for says in (("हाँ",), ("yes",), ("बिल्कुल",), ("सही", "सही है")):
        outcome = talked_over((), opening=QUESTION, caller=Caller(says, after=TAIL))
        assert outcome == Outcome(interrupted=False, turns=[says[-1]], paused=1)


def test_an_acknowledgement_over_the_end_of_a_statement_is_still_dropped() -> None:
    outcome = talked_over((), caller=Caller(("हम्म",), after=TAIL))
    assert outcome == Outcome(interrupted=False, turns=[], paused=1)


def test_an_acknowledgement_long_before_the_question_is_not_its_answer() -> None:
    outcome = talked_over((), opening=PLAN_THEN_QUESTION, caller=Caller(("हाँ",)), read=2 * READ_S)
    assert outcome == Outcome(interrupted=False, turns=[], paused=1)


def test_an_answer_the_caller_goes_on_with_is_one_turn() -> None:
    caller = Caller(("हाँ",), after=TAIL, again=("हाँ, कर दीजिए",))
    outcome = talked_over((), opening=QUESTION, caller=caller)
    assert outcome.interrupted
    assert outcome.turns == ["हाँ, कर दीजिए"]


def test_a_called_agent_hears_an_acknowledgement_over_the_end_of_its_question_as_the_answer() -> (
    None
):
    outcome = called_over(Caller(("हाँ",), after=TAIL), QUESTION)
    assert outcome == Outcome(interrupted=False, turns=["हाँ"], paused=1)


def test_a_called_agent_still_drops_an_acknowledgement_over_a_statement() -> None:
    outcome = called_over(Caller(("हाँ", "हाँ जी"), after=TAIL), OPENING)
    assert outcome == Outcome(interrupted=False, turns=[], paused=1)


def test_a_called_agent_drops_an_acknowledgement_long_before_its_question() -> None:
    outcome = called_over(Caller(("हाँ",)), PLAN_THEN_QUESTION, read=2 * READ_S)
    assert outcome == Outcome(interrupted=False, turns=[], paused=1)
