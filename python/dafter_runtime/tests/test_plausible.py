from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from dafter_runtime.plausible import Plausible, looped, too_fast
from livekit.agents import LanguageCode, stt

HUM_AS_NAME = " ".join(["నివ్య"] * 85)


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def final(text: str, start: float = 0.0, end: float = 0.0) -> stt.SpeechEvent:
    data = stt.SpeechData(language=LanguageCode("te"), text=text, start_time=start, end_time=end)
    return stt.SpeechEvent(type=stt.SpeechEventType.FINAL_TRANSCRIPT, alternatives=[data])


def test_one_word_said_over_and_over_is_not_speech() -> None:
    assert looped(HUM_AS_NAME)
    assert not looped("నివ్య నువ్వు ఏం చేయగలవు?")
    assert not looped("no no no no")
    assert not looped("")


def test_more_letters_a_second_than_anyone_can_speak_is_not_speech() -> None:
    assert too_fast("x" * 505, 1.75)
    assert not too_fast("నివ్య, ఒక story చెప్పు, కొంచెం పెద్దది కావాలి, సరేనా?", 3.0)
    assert not too_fast("short", 0.05)


def test_an_impossible_transcript_never_reaches_the_call_and_the_next_one_does() -> None:
    clock = Clock()
    plausible = Plausible(clock)
    start = stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH)
    sent: list[stt.SpeechEvent] = [
        start,
        final(HUM_AS_NAME),
        start,
        final("x" * 120, start=10.0, end=11.0),
        start,
        final("నివ్య, ఒక story చెప్పు", start=20.0, end=22.0),
    ]

    async def hear() -> list[stt.SpeechEvent | str]:
        async def events() -> AsyncIterator[stt.SpeechEvent | str]:
            for event in sent:
                clock.now += 1
                yield event

        return [e async for e in plausible(events())]

    assert asyncio.run(hear()) == [start, start, start, sent[5]]
