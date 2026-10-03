from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from typing import Any, cast

from dafter_runtime.backchannel import Events
from dafter_runtime.naming import words
from dafter_runtime.own_voice import OwnVoice, missed, recognized_as
from livekit.agents import AgentSession, LanguageCode, stt
from livekit.agents.voice.events import UserStateChangedEvent

REPLY = "Keep your rental agreement, the legal notice and the rent receipts ready."
FILLER = "Okay, let me check."


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Listening:
    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[Any], None]] = {}

    def on(self, event: str, handler: Callable[[Any], None]) -> None:
        self.handlers[event] = handler

    def starts_speaking(self) -> None:
        self.handlers["user_state_changed"](
            UserStateChangedEvent(old_state="listening", new_state="speaking")
        )


def speaking(clock: Clock, *texts: str) -> OwnVoice:
    voice = OwnVoice(clock)
    voice.speaking()
    for text in texts:
        voice.said(text)
    return voice


def test_a_word_counts_when_it_is_what_was_said_cut_short_or_slightly_misheard() -> None:
    assert recognized_as("secon", "second")
    assert recognized_as("keeps", "keep")
    assert recognized_as("receits", "receipts")
    assert not recognized_as("deposit", "notice")
    assert not recognized_as("ten", "rent")


def test_missed_counts_the_heard_words_the_agent_never_said() -> None:
    said = frozenset(words(f"{FILLER} {REPLY}"))
    assert missed(words("Okay, let me check. Keep your rental"), said) == 0
    assert missed(words("Keep your hand there like this"), said) == 4
    assert missed((), said) == 0


def test_the_agent_heard_back_while_it_speaks_is_its_own_voice() -> None:
    clock = Clock()
    voice = speaking(clock, FILLER, REPLY)
    clock.now += 2
    assert voice.echoes("Okay, let me check. Keep your rental agreement", onset=clock.now - 0.5)
    assert voice.echoes("the legal notice and the rent receits", onset=clock.now - 0.5)


def test_a_person_talking_over_the_agent_is_still_heard() -> None:
    clock = Clock()
    voice = speaking(clock, REPLY)
    clock.now += 2
    assert not voice.echoes("Wait, I have a question about my deposit", onset=clock.now - 0.5)


def test_an_answer_that_starts_after_the_agent_stops_is_heard_even_in_its_words() -> None:
    clock = Clock()
    voice = speaking(clock, "Do you have the rent receipts ready, yes or no?")
    clock.now += 3
    voice.quiet()
    clock.now += 1
    assert not voice.echoes("Yes, the rent receipts are ready", onset=clock.now - 0.3)
    assert voice.echoes("the rent receipts ready, yes or no", onset=clock.now - 0.9)


def test_nothing_is_dropped_before_the_agent_speaks_or_without_a_known_start() -> None:
    clock = Clock()
    voice = OwnVoice(clock)
    voice.said(REPLY)
    assert not voice.echoes(REPLY, onset=clock.now)
    voice.speaking()
    assert not voice.echoes(REPLY, onset=None)


def test_a_line_known_to_echo_needs_three_new_words_over_the_agent() -> None:
    clock = Clock()
    voice = speaking(clock, REPLY)
    clock.now += 1
    onset = clock.now - 0.2
    assert not voice.echoes("complete", onset)
    assert voice.echoes("complete", onset, echoing=True)
    assert voice.echoes("the rent, hmm, listen", onset, echoing=True)
    assert not voice.echoes("hold on, wait", onset, echoing=True)


def test_text_streamed_to_the_voice_is_matched_as_whole_words() -> None:
    clock = Clock()
    voice = OwnVoice(clock)
    voice.speaking()

    async def chunks() -> AsyncIterator[str]:
        for chunk in ("Keep your ren", "tal agree", "ment ready."):
            yield chunk

    async def spoken() -> list[str]:
        return [c async for c in voice.saying(chunks())]

    assert asyncio.run(spoken()) == ["Keep your ren", "tal agree", "ment ready."]
    assert voice.echoes("rental agreement", onset=clock.now)


def test_what_was_said_long_ago_no_longer_matches() -> None:
    clock = Clock()
    voice = speaking(clock, REPLY)
    for _ in range(3):
        voice.said("Is there anything else?")
    clock.now += 60
    assert not voice.echoes("rental agreement legal notice", onset=clock.now)


def transcript(kind: stt.SpeechEventType, text: str) -> stt.SpeechEvent:
    data = stt.SpeechData(language=LanguageCode("en"), text=text)
    return stt.SpeechEvent(type=kind, alternatives=[data])


def test_the_ear_drops_the_echo_and_reports_the_line_once() -> None:
    clock = Clock()
    voice = speaking(clock, REPLY)
    listening = Listening()
    echoed: list[bool] = []
    ear = voice.hearing(cast(AgentSession[Any], listening), lambda: echoed.append(True))
    listening.starts_speaking()
    final = stt.SpeechEventType.FINAL_TRANSCRIPT
    sent = [
        stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH),
        transcript(stt.SpeechEventType.INTERIM_TRANSCRIPT, "Keep your rental"),
        transcript(final, "Keep your rental agreement"),
        transcript(final, "Wait, I have a question"),
    ]

    async def hear() -> list[stt.SpeechEvent | str]:
        async def events() -> AsyncIterator[stt.SpeechEvent | str]:
            for event in sent:
                yield event

        heard: Events = ear(events())
        return [e async for e in heard]

    assert asyncio.run(hear()) == [sent[0], sent[3]]
    assert echoed == [True]
