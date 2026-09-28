from __future__ import annotations

import asyncio
from typing import Any

from dafter_core.switching import LanguageSwitching
from dafter_runtime.personas import Persona
from dafter_runtime.switching import Switching
from dafter_runtime.timing import Turns
from livekit.agents import LanguageCode, llm, stt

SWITCHING = LanguageSwitching(enabled=True, languages=("hi", "en-IN", "kn-IN"), min_words=3)
PERSONAS = {
    "hi": Persona("reply in Hindi", "नमस्ते"),
    "en-IN": Persona("reply in English", "Hello"),
    "kn-IN": Persona("reply in Kannada", "ನಮಸ್ಕಾರ"),
}
ENGLISH = "can you tell me the time please"
KANNADA = "ನನಗೆ ಸಮಯ ಹೇಳಿ ದಯವಿಟ್ಟು"


def final(text: str, language: str, confidence: float | None = None) -> stt.SpeechEvent:
    metadata = None if confidence is None else {"language_confidence": confidence}
    data = stt.SpeechData(language=LanguageCode(language), text=text, metadata=metadata)
    return stt.SpeechEvent(type=stt.SpeechEventType.FINAL_TRANSCRIPT, alternatives=[data])


def switching(config: LanguageSwitching = SWITCHING) -> tuple[Switching, list[str]]:
    s = Switching(config, "hi", PERSONAS)
    followed: list[str] = []
    s.follow_with(followed.append)
    return s, followed


def test_an_identified_final_in_another_listed_language_switches_by_base_language() -> None:
    s, followed = switching()
    s.heard(None, final(ENGLISH, "en-IN", 0.93))
    assert (s.language, followed) == ("en-IN", ["en-IN"])
    assert s.persona.instructions == "reply in English"
    s.heard(None, final(KANNADA, "kn-IN"))
    assert s.language == "kn-IN"
    s.heard(None, final("मुझे समय बताइए अभी", "hi-IN", 0.99))
    assert followed == ["en-IN", "kn-IN", "hi"]


def test_what_says_little_about_the_language_never_switches() -> None:
    s, followed = switching()
    s.heard(None, final("ok thank you", "en-IN", 0.5))
    s.heard(None, final("ok thanks", "en-IN", 0.99))
    s.heard(None, final(ENGLISH, "ta-IN", 0.99))
    s.heard(None, final(ENGLISH, "auto"))
    interim = stt.SpeechEvent(
        type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
        alternatives=[stt.SpeechData(language=LanguageCode("en-IN"), text=ENGLISH)],
    )
    s.heard(None, interim)
    assert (s.language, followed) == ("hi", [])


def test_a_session_that_does_not_switch_never_follows() -> None:
    s, followed = switching(LanguageSwitching(enabled=False, languages=("hi", "en-IN")))
    s.heard(None, final(ENGLISH, "en-IN", 0.99))
    assert not s.ask("en-IN")
    assert (s.language, followed, s.languages) == ("hi", [], [])


def test_a_language_asked_for_holds_until_another_is_asked_for() -> None:
    s, followed = switching()
    assert s.ask("en-IN")
    s.heard(None, final(KANNADA, "kn-IN", 0.99))
    assert s.language == "en-IN"
    assert not s.ask("ta-IN")
    assert s.ask("kn-IN")
    assert followed == ["en-IN", "kn-IN"]


def test_in_a_call_the_language_follows_the_person_answered() -> None:
    s, followed = switching()
    s.heard("p_asha", final(ENGLISH, "en-IN", 0.9))
    s.heard("p_ravi", final(KANNADA, "kn-IN", 0.9))
    assert followed == []
    s.answering("p_ravi")
    assert s.language == "kn-IN"
    s.answering("p_asha")
    s.answering("p_nobody")
    assert followed == ["kn-IN", "en-IN"]


def test_observing_passes_every_event_through_in_order() -> None:
    s, followed = switching()
    events: list[Any] = [
        stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH),
        final(ENGLISH, "en-IN", 0.9),
        "a word",
        stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH),
    ]

    async def run() -> list[Any]:
        async def source() -> Any:
            for event in events:
                yield event

        return [event async for event in s.observe()(source())]

    assert asyncio.run(run()) == events
    assert followed == ["en-IN"]


def test_a_reply_is_measured_in_the_language_decided_at_the_turn_it_answers() -> None:
    turns = Turns()
    assert turns.add(llm.ChatMessage(role="user", content=[ENGLISH]), language="en-IN") is None
    reply = turns.add(llm.ChatMessage(role="assistant", content=["Sure."]), language="hi")
    greeting = turns.add(llm.ChatMessage(role="assistant", content=["नमस्ते"]), language="hi")
    pinned = turns.add(llm.ChatMessage(role="assistant", content=["नमस्ते"]))
    assert reply is not None and reply.payload()["language"] == "en-IN"
    assert greeting is not None and greeting.payload()["language"] == "hi"
    assert pinned is not None and "language" not in pinned.payload()
