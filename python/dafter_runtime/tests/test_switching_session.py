from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError
from dafter_core.hashing import seal
from dafter_runtime.answering import Roster
from dafter_runtime.backchannel import Acknowledgements
from dafter_runtime.delivery import Delivery
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.speech_plan import SpeechPlan
from dafter_runtime.switching import Switching
from dafter_runtime.timing import Turns
from dafter_runtime.toolbox import Answering, everyday, registry_for
from livekit.agents import AgentSession, LanguageCode, llm
from livekit.agents.voice.generation import INSTRUCTIONS_MESSAGE_ID
from session_rig import Microphone, ScriptedSTT, SlowReader, Speaker, until, worker_turn_handling
from stub_llm import StubLLM

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
ENGLISH = LanguageCode("en-IN")


class Polyglot(SlowReader):
    def __init__(self) -> None:
        super().__init__(0.2)
        self.languages: list[str] = []

    def speak_in(self, language: str) -> None:
        self.languages.append(language)


def switching_job(**switching: Any) -> bytes:
    doc = json.loads(JOB.read_bytes())
    doc["agent"]["languageSwitching"] = {"enabled": True, "languages": ["hi", "en-IN"]} | switching
    sealed, _ = seal(json.dumps(doc))
    return sealed


def refused(raw: bytes) -> DafterError:
    with pytest.raises(DafterError) as caught:
        plan(load(raw), "dafter-py")
    return caught.value


def instructions(ctx: llm.ChatContext) -> str:
    item = ctx.get_by_id(INSTRUCTIONS_MESSAGE_ID)
    assert isinstance(item, llm.ChatMessage)
    return item.text_content or ""


def test_a_switching_job_hears_through_identification_and_plans_a_persona_per_language() -> None:
    p = plan(load(switching_job()), "dafter-py")
    assert p.hearing is None
    assert set(p.personas) == {"hi", "en-IN"}
    assert "Hindi" in p.personas["hi"].instructions
    assert "English" in p.personas["en-IN"].instructions
    pinned = plan(load(JOB.read_bytes().strip()), "dafter-py")
    assert (pinned.hearing, set(pinned.personas)) == ("hi", {"hi"})


@pytest.mark.parametrize(
    ("languages", "because"),
    [
        (["hi", "ta-IN"], "persona"),
        (["hi", "fr-FR"], "provider"),
        (["hi", "hi-IN"], "base language"),
    ],
)
def test_a_language_the_worker_cannot_answer_in_is_refused_before_it_joins(
    languages: list[str], because: str
) -> None:
    err = refused(switching_job(languages=languages))
    assert "/agent/languageSwitching/languages/1" in err.details[0]
    assert because in err.message
    assert err.code in {ErrorCode.UNSUPPORTED_CAPABILITY, ErrorCode.INVALID_CONFIG}


def converse(p: Plan) -> tuple[list[str], list[str], Polyglot, list[str], list[str]]:
    async def run() -> tuple[list[str], list[str], Polyglot, list[str], list[str]]:
        recognizer, voice, model = ScriptedSTT(), Polyglot(), StubLLM(reply="Sure.")
        session: AgentSession[Any] = AgentSession(
            stt=recognizer,
            llm=model,
            tts=voice,
            turn_handling=worker_turn_handling(p),  # type: ignore[arg-type]
            user_away_timeout=None,
            aec_warmup_duration=None,
        )
        session.input.audio = Microphone()
        session.output.audio = Speaker()
        switching = Switching(p.config.agent.language_switching, p.config.language, p.personas)
        spoken = SpeechPlan(p.config.agent.speech, p.config.language)
        switching.follow_with(voice.speak_in)
        switching.follow_with(spoken.speak_in)
        delivery = Delivery(p.config.agent.speech, p.config.language)
        registry = registry_for(p, session, Roster(), lambda: None, None, delivery, switching)
        heard = Acknowledgements.of(p.config.turn.interruption.backchannel)
        agent = Answering(
            p.persona.instructions, registry, lambda: None, heard, delivery, switching
        )
        turns, languages = Turns(), []

        def added(ev: Any) -> None:
            timing = turns.add(ev.item, language=switching.language)
            if timing is not None:
                languages.append(timing.payload()["language"])

        session.on("conversation_item_added", added)
        await session.start(agent, record=False)
        numbers: list[str] = []
        recognizer.says("please tell me the time now", language=ENGLISH, confidence=0.95)
        await until(lambda: len(model.requests) == 1)
        numbers.append(spoken.spoken("₹1,25,000"))
        await until(lambda: len(languages) == 1)
        recognizer.says("अब मुझे हिंदी में समय बताइए", language=LanguageCode("hi-IN"))
        await until(lambda: len(model.requests) == 2)
        numbers.append(spoken.spoken("₹1,25,000"))
        await until(lambda: len(languages) == 2)
        await session.aclose()
        return (
            [instructions(r) for r in model.requests],
            numbers,
            voice,
            languages,
            [t.id for t in agent.tools],
        )

    return asyncio.run(run())


def test_the_agent_replies_in_the_language_the_caller_switched_to() -> None:
    asked, numbers, voice, languages, tools = converse(plan(load(switching_job()), "dafter-py"))
    assert "Reply only in English" in asked[0]
    assert "Reply only in Hindi" in asked[1]
    assert voice.languages == ["en-IN", "hi"]
    assert numbers[0] == "one lakh twenty five thousand rupees"
    assert "लाख" in numbers[1]
    assert languages == ["en-IN", "hi"]
    assert "switch_language" in tools


def test_the_caller_asking_for_a_language_holds_it() -> None:
    p = plan(load(switching_job()), "dafter-py")
    switching = Switching(p.config.agent.language_switching, p.config.language, p.personas)
    tools = {t.name: t for t in everyday(Roster(), None, switching)}
    ask = tools["switch_language"]
    assert ask.parameters["properties"]["language"]["enum"] == ["hi", "en-IN"]
    assert asyncio.run(ask.run({"language": "en-IN"})).startswith("Switched")
    assert switching.language == "en-IN"
    assert asyncio.run(ask.run({"language": "ta-IN"})).startswith("Not switched")
    assert switching.language == "en-IN"
    assert "switch_language" not in {t.name for t in everyday(Roster(), None, None)}
