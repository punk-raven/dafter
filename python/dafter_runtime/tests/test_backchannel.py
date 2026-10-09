from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import pytest
from dafter_core.config import parse
from dafter_runtime.backchannel import (
    Acknowledgements,
    Reply,
    Sieve,
    acknowledged,
    holds_floor,
    is_question,
    sieved,
    text_of,
)
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


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class Floor:
    def __init__(self, held: bool = True) -> None:
        self.holding = held
        self.hearing = True
        self.clock = Clock()
        self.reply = Reply(self.clock)
        self.reply.playing(True)
        self.cuts = 0

    def held(self) -> bool:
        return self.holding

    def cut(self) -> None:
        self.cuts += 1
        self.holding = False

    def heard(self) -> Reply | None:
        return self.reply if self.holding and self.hearing else None


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


@pytest.mark.parametrize(
    "text", ["sari", "sare", "avunu", "houdu", "barobar", "mm-hmm", "haanji", "yeah"]
)
def test_the_lexicon_adds_romanized_acknowledgements_the_recognizer_may_write(text: str) -> None:
    assert catalog().only(text, finished=True)


@pytest.mark.parametrize("text", ["no no", "nahi", "illa", "thamba", "haan nahi", "okay wait"])
def test_a_negative_is_never_an_acknowledgement(text: str) -> None:
    assert not catalog().only(text, finished=True)
    assert catalog().yields(text)


def test_an_affirmative_with_more_after_it_yields_and_one_alone_does_not() -> None:
    acknowledgements = catalog()
    assert acknowledgements.yields("हाँ लेकिन रुकिए")
    assert acknowledgements.yields("achha ek baat")
    assert not acknowledgements.yields("हाँ जी")
    assert not acknowledgements.yields("एक बात बताइए")


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
    floor.holding = False
    passed = run(sieve, event(KIND.END_OF_SPEECH))
    assert [kind for kind, _ in passed] == ["start_of_speech", "final_transcript", "end_of_speech"]
    assert dropped == []


def test_an_utterance_that_began_while_the_floor_was_free_is_never_held() -> None:
    floor = Floor(held=False)
    sieve = Sieve(catalog(), floor)
    run(sieve, event(KIND.START_OF_SPEECH))
    floor.holding = True
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


def said_over(sieve: Sieve, text: str = "हाँ") -> list[tuple[str, str]]:
    return run(
        sieve,
        event(KIND.START_OF_SPEECH),
        event(KIND.FINAL_TRANSCRIPT, text),
        event(KIND.END_OF_SPEECH),
    )


def answers(sieve: Sieve) -> list[str]:
    return [text_of(e) or str(e.type.value) for e in sieve.answered()]


def test_an_acknowledgement_over_the_end_of_a_question_is_released_once_it_ends() -> None:
    floor = Floor()
    sieve = Sieve(catalog(), floor)
    floor.clock.now = 3.0
    assert said_over(sieve) == []
    assert not sieve.answering.is_set()
    floor.clock.now = 3.0 + catalog().answer_within
    floor.reply.over(asked=True)
    assert sieve.answering.is_set()
    assert answers(sieve) == ["start_of_speech", "हाँ", "end_of_speech"]
    assert not sieve.answering.is_set()
    assert answers(sieve) == []


def test_an_acknowledgement_over_a_statement_or_a_cut_reply_stays_dropped() -> None:
    floor = Floor()
    sieve = Sieve(catalog(), floor)
    said_over(sieve)
    floor.reply.over(asked=False)
    assert answers(sieve) == []


def test_an_acknowledgement_said_long_before_the_question_ends_stays_dropped() -> None:
    floor = Floor()
    sieve = Sieve(catalog(), floor)
    said_over(sieve)
    floor.clock.now = catalog().answer_within + 0.01
    floor.reply.over(asked=True)
    assert answers(sieve) == []


def test_time_the_reply_spends_paused_does_not_count_against_the_answer() -> None:
    floor = Floor()
    sieve = Sieve(catalog(), floor)
    floor.reply.playing(False)
    said_over(sieve)
    floor.clock.now = 5.0
    floor.reply.playing(True)
    floor.clock.now = 5.5
    floor.reply.over(asked=True)
    assert answers(sieve) == ["start_of_speech", "हाँ", "end_of_speech"]


def test_the_caller_speaking_again_before_the_question_ends_takes_the_turn_instead() -> None:
    floor = Floor()
    sieve = Sieve(catalog(), floor)
    said_over(sieve)
    passed = said_over(sieve, "हाँ, कर दीजिए")
    floor.reply.over(asked=True)
    assert [text for _, text in passed] == ["", "हाँ, कर दीजिए", ""]
    assert answers(sieve) == []


def test_a_reply_the_caller_had_not_heard_yet_is_not_answered() -> None:
    floor = Floor()
    floor.hearing = False
    sieve = Sieve(catalog(), floor)
    run(sieve, event(KIND.START_OF_SPEECH), event(KIND.FINAL_TRANSCRIPT, "हाँ"))
    floor.hearing = True
    run(sieve, event(KIND.END_OF_SPEECH))
    floor.reply.over(asked=True)
    assert answers(sieve) == []


@pytest.mark.parametrize(
    ("text", "question"),
    [
        ("क्या मैं आपका ऑर्डर कैंसिल कर दूँ?", True),
        ("Shall I cancel it? ", True),
        ("ठीक है न\uff1f", True),
        ("आप आएँगे?।", True),
        ('क्या आप "हाँ" कहेंगे?"', True),
        ("मैं ऑर्डर कैंसिल कर देती हूँ।", False),
        ("क्या? मैं बताती हूँ।", False),
        ("", False),
    ],
)
def test_a_reply_asks_when_it_ends_in_a_question_mark(text: str, question: bool) -> None:
    assert is_question(text) is question


def test_the_stream_yields_an_answer_when_the_reply_ends_with_no_new_event() -> None:
    floor = Floor()
    sieve = Sieve(catalog(), floor)

    async def collect() -> list[str]:
        caller: asyncio.Queue[stt.SpeechEvent | None] = asyncio.Queue()

        async def events() -> AsyncIterator[stt.SpeechEvent | str]:
            while (e := await caller.get()) is not None:
                yield e

        seen: list[str] = []

        async def read() -> None:
            async for e in sieved(events(), sieve):
                assert isinstance(e, stt.SpeechEvent)
                seen.append(text_of(e) or str(e.type.value))

        reading = asyncio.ensure_future(read())
        for kind, text in ((KIND.START_OF_SPEECH, ""), (KIND.FINAL_TRANSCRIPT, "हाँ")):
            caller.put_nowait(event(kind, text))
        caller.put_nowait(event(KIND.END_OF_SPEECH))
        await asyncio.sleep(0.01)
        assert seen == []
        floor.reply.over(asked=True)
        await asyncio.sleep(0.01)
        caller.put_nowait(None)
        await reading
        return seen

    assert asyncio.run(collect()) == ["start_of_speech", "हाँ", "end_of_speech"]


def test_a_negative_over_the_reply_cuts_it_when_the_sieve_cuts() -> None:
    floor = Floor()
    sieve = Sieve(catalog(), floor, cuts=True)
    passed = run(
        sieve,
        event(KIND.START_OF_SPEECH),
        event(KIND.INTERIM_TRANSCRIPT, "no"),
        event(KIND.FINAL_TRANSCRIPT, "no no"),
        event(KIND.END_OF_SPEECH),
    )
    assert floor.cuts == 1
    assert [text for _, text in passed] == ["", "no", "no no", ""]


def test_a_mixed_cue_cuts_the_reply_once_it_goes_past_the_affirmative() -> None:
    floor = Floor()
    sieve = Sieve(catalog(), floor, cuts=True)
    run(sieve, event(KIND.START_OF_SPEECH), event(KIND.INTERIM_TRANSCRIPT, "हाँ"))
    assert floor.cuts == 0
    run(sieve, event(KIND.INTERIM_TRANSCRIPT, "हाँ लेकिन"))
    assert floor.cuts == 1


def test_a_negative_in_speech_already_passing_still_cuts_the_reply() -> None:
    floor = Floor()
    sieve = Sieve(catalog(), floor, cuts=True)
    run(sieve, event(KIND.START_OF_SPEECH), event(KIND.INTERIM_TRANSCRIPT, "एक"))
    assert floor.cuts == 0
    run(sieve, event(KIND.INTERIM_TRANSCRIPT, "एक नहीं"))
    assert floor.cuts == 1


def test_a_sieve_that_does_not_cut_leaves_the_reply_to_the_barge_in() -> None:
    floor = Floor()
    sieve = Sieve(catalog(), floor)
    run(sieve, event(KIND.START_OF_SPEECH), event(KIND.FINAL_TRANSCRIPT, "wait"))
    assert floor.cuts == 0


def test_an_acknowledgement_alone_never_cuts_the_reply() -> None:
    floor = Floor()
    sieve = Sieve(catalog(), floor, cuts=True)
    run(
        sieve,
        event(KIND.START_OF_SPEECH),
        event(KIND.FINAL_TRANSCRIPT, "हाँ जी"),
        event(KIND.END_OF_SPEECH),
    )
    assert floor.cuts == 0
