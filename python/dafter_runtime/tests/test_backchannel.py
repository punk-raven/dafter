from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest
from dafter_core.config import parse
from dafter_runtime.backchannel import Acknowledgements, Sieve, acknowledged, holds_floor
from livekit.agents import Agent, AgentSession, LanguageCode, stt
from stub_llm import StubLLM

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
KIND = stt.SpeechEventType
HINDI = LanguageCode("hi")


def catalog() -> Acknowledgements:
    acknowledgements = Acknowledgements.of(parse(JOB.read_bytes()).turn.interruption.backchannel)
    assert acknowledgements is not None
    return acknowledgements


def event(kind: stt.SpeechEventType, text: str = "") -> stt.SpeechEvent:
    if not text and kind not in TEXT:
        return stt.SpeechEvent(type=kind)
    return stt.SpeechEvent(type=kind, alternatives=[stt.SpeechData(HINDI, text)])


TEXT = frozenset({KIND.INTERIM_TRANSCRIPT, KIND.FINAL_TRANSCRIPT, KIND.PREFLIGHT_TRANSCRIPT})


class Floor:
    def __init__(self, held: bool = True) -> None:
        self.held = held

    def __call__(self) -> bool:
        return self.held


def run(sieve: Sieve, *events: stt.SpeechEvent) -> list[tuple[str, str]]:
    passed = [e for ev in events for e in sieve.feed(ev)]
    return [(str(e.type.value), e.alternatives[0].text if e.alternatives else "") for e in passed]


@pytest.mark.parametrize(
    "text",
    [
        "हाँ",
        "हां जी",
        "हम्म",
        "अच्छा",
        "ठीक है",
        "हाँ हाँ",
        "जी हाँ",
        "ok",
        "Okay.",
        "uh-huh",
        "ಹೌದು",
        "हो",
        "సరే",
        "achha",
        "theek hai",
    ],
)
def test_the_catalog_hears_each_languages_acknowledgements(text: str) -> None:
    assert catalog().only(text, finished=True)


@pytest.mark.parametrize(
    "text",
    ["हाँ लेकिन रुकिए", "नहीं", "रुको", "है", "ठीक", "हाँ हाँ हाँ ठीक है", "wait", "", "   "],
)
def test_anything_else_is_a_turn(text: str) -> None:
    assert not catalog().only(text, finished=True)


def test_an_unfinished_word_or_phrase_is_still_in_doubt() -> None:
    acknowledgements = catalog()
    assert acknowledgements.only("ठीक", finished=False)
    assert acknowledgements.only("हम्", finished=False)
    assert not acknowledgements.only("रु", finished=False)


def test_a_disabled_or_empty_list_hears_no_acknowledgement() -> None:
    backchannel = parse(JOB.read_bytes()).turn.interruption.backchannel
    assert Acknowledgements.of(replace(backchannel, enabled=False)) is None
    assert Acknowledgements.of(replace(backchannel, words={})) is None


def test_an_acknowledgement_while_the_agent_talks_never_becomes_a_turn() -> None:
    dropped: list[bool] = []
    sieve = Sieve(catalog(), Floor(), lambda: dropped.append(True))
    passed = run(
        sieve,
        event(KIND.START_OF_SPEECH),
        event(KIND.INTERIM_TRANSCRIPT, "हाँ"),
        event(KIND.FINAL_TRANSCRIPT, "हाँ जी"),
        event(KIND.RECOGNITION_USAGE),
        event(KIND.END_OF_SPEECH),
    )
    assert passed == [("recognition_usage", "")]
    assert dropped == [True]


def test_real_words_release_everything_held_in_order_and_pass_the_rest() -> None:
    sieve = Sieve(catalog(), Floor())
    passed = run(
        sieve,
        event(KIND.START_OF_SPEECH),
        event(KIND.INTERIM_TRANSCRIPT, "हाँ"),
        event(KIND.INTERIM_TRANSCRIPT, "हाँ लेकिन"),
        event(KIND.FINAL_TRANSCRIPT, "हाँ लेकिन रुकिए"),
        event(KIND.END_OF_SPEECH),
    )
    assert passed == [
        ("start_of_speech", ""),
        ("interim_transcript", "हाँ"),
        ("interim_transcript", "हाँ लेकिन"),
        ("final_transcript", "हाँ लेकिन रुकिए"),
        ("end_of_speech", ""),
    ]


def test_a_second_final_that_is_not_an_acknowledgement_carries_the_first_with_it() -> None:
    sieve = Sieve(catalog(), Floor())
    passed = run(
        sieve,
        event(KIND.START_OF_SPEECH),
        event(KIND.FINAL_TRANSCRIPT, "अच्छा"),
        event(KIND.FINAL_TRANSCRIPT, "एक बात बताइए"),
        event(KIND.END_OF_SPEECH),
    )
    assert [kind for kind, _ in passed] == [
        "start_of_speech",
        "final_transcript",
        "final_transcript",
        "end_of_speech",
    ]


def test_with_the_floor_free_an_acknowledgement_is_an_answer() -> None:
    sieve = Sieve(catalog(), Floor(held=False))
    passed = run(
        sieve,
        event(KIND.START_OF_SPEECH),
        event(KIND.FINAL_TRANSCRIPT, "हाँ"),
        event(KIND.END_OF_SPEECH),
    )
    assert [text for _, text in passed] == ["", "हाँ", ""]


def test_an_acknowledgement_the_agent_finished_during_is_an_answer() -> None:
    floor = Floor()
    dropped: list[bool] = []
    sieve = Sieve(catalog(), floor, lambda: dropped.append(True))
    held = run(sieve, event(KIND.START_OF_SPEECH), event(KIND.FINAL_TRANSCRIPT, "हाँ"))
    assert held == []
    floor.held = False
    passed = run(sieve, event(KIND.END_OF_SPEECH))
    assert [kind for kind, _ in passed] == ["start_of_speech", "final_transcript", "end_of_speech"]
    assert dropped == []


def test_an_utterance_that_began_while_the_floor_was_free_is_never_held() -> None:
    floor = Floor(held=False)
    sieve = Sieve(catalog(), floor)
    run(sieve, event(KIND.START_OF_SPEECH))
    floor.held = True
    passed = run(sieve, event(KIND.FINAL_TRANSCRIPT, "हाँ"), event(KIND.END_OF_SPEECH))
    assert [kind for kind, _ in passed] == ["final_transcript", "end_of_speech"]


def test_noise_the_recognizer_never_transcribed_is_dropped_without_an_acknowledgement() -> None:
    dropped: list[bool] = []
    sieve = Sieve(catalog(), Floor(), lambda: dropped.append(True))
    assert run(sieve, event(KIND.START_OF_SPEECH), event(KIND.END_OF_SPEECH)) == []
    assert dropped == []


def test_the_filter_passes_a_stream_through_untouched_without_acknowledgements() -> None:
    async def events() -> AsyncIterator[stt.SpeechEvent | str]:
        yield event(KIND.FINAL_TRANSCRIPT, "हाँ")

    async def collect() -> list[stt.SpeechEvent | str]:
        return [e async for e in acknowledged(None, Floor())(events())]

    [only] = asyncio.run(collect())
    assert isinstance(only, stt.SpeechEvent)


def test_the_floor_is_held_while_a_reply_is_pending_or_playing_and_free_once_cut() -> None:
    async def run_session() -> None:
        async with AgentSession[None](llm=StubLLM()) as session:
            await session.start(Agent(instructions=""))
            assert not holds_floor(session)
            reply = session.generate_reply(user_input="नमस्ते")
            for _ in range(100):
                if session.current_speech is not None:
                    break
                await asyncio.sleep(0)
            assert holds_floor(session)
            reply.interrupt()
            assert not holds_floor(session)
            await reply

    asyncio.run(run_session())
