from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dafter_core.hashing import seal
from dafter_runtime.answering import Roster
from dafter_runtime.backchannel import Acknowledgements
from dafter_runtime.plan import load, plan
from dafter_runtime.toolbox import Answering, registry_for
from livekit.agents import AgentSession, llm
from session_rig import Microphone, ScriptedSTT, ScriptedVAD, SlowReader, Speaker, until
from stub_llm import StubLLM

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
RESUME_MS = 300
READ_S = 1.5
OPENING = "नमस्ते, मैं आपको आज की योजना के बारे में बताता हूँ।"


@dataclass(frozen=True)
class Outcome:
    interrupted: bool
    turns: list[str]
    paused: int


def talked_over(caller_says: tuple[str, ...], acknowledgements: bool = True) -> Outcome:
    doc = json.loads(JOB.read_bytes())
    doc["turn"]["interruption"]["falseInterruptionTimeoutMs"] = RESUME_MS
    doc["turn"]["interruption"]["backchannel"]["enabled"] = acknowledgements
    sealed, _ = seal(json.dumps(doc))
    p = plan(load(sealed), "dafter-py")

    async def run() -> Outcome:
        recognizer, detector, speaker = ScriptedSTT(), ScriptedVAD(), Speaker()
        session: AgentSession[Any] = AgentSession(
            stt=recognizer,
            llm=StubLLM(),
            tts=SlowReader(READ_S),
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
        opening = session.say(OPENING)
        await until(lambda: session.agent_state == "speaking")
        detector.talks(0.3)
        await until(lambda: speaker.paused)
        recognizer.says(*caller_says)
        detector.stops()
        await asyncio.wait_for(_done(opening), 10)
        await asyncio.sleep(0.2)
        turns = [
            m.text_content or ""
            for m in session.history.items
            if isinstance(m, llm.ChatMessage) and m.role == "user"
        ]
        outcome = Outcome(opening.interrupted, turns, speaker.pauses)
        await session.aclose()
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
