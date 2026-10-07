from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator

import pytest
from dafter_runtime.history import (
    HISTORY_ITEMS,
    instructions_of,
    last_turn,
    recovering,
    repeats,
    replies,
    tidy,
    unrepeated,
    well_formed,
)
from livekit.agents import APIStatusError, llm

CLASS_ANSWER = "అరె సారీ అండి నేను ఒక AIని నాకు డైరెక్ట్గా క్లాస్ ఎత్తే ఆప్షన్ ఉండదు, మీ ప్రొఫెసర్ని కానీ అడ్మిన్ని కానీ కాంటాక్ట్ చేయండి."
STORY_ONE = "Sure, here it goes. There was once a king who loved his garden."
STORY_TWO = "Sure, here's another one. A clever fox lived near a big river."


def call(*turns: tuple[str, str, bool]) -> llm.ChatContext:
    ctx = llm.ChatContext()
    ctx.add_message(role="system", content="You are Nivya.")
    for role, text, interrupted in turns:
        ctx.add_message(role=role, content=text, interrupted=interrupted)  # type: ignore[arg-type]
    return ctx


def said(ctx: llm.ChatContext) -> list[tuple[str, str]]:
    return [
        (item.role, item.text_content or "")
        for item in ctx.items
        if isinstance(item, llm.ChatMessage)
    ]


def test_replies_cut_off_after_a_word_or_two_are_left_out_of_what_she_reads() -> None:
    ctx = call(
        ("user", "నివ్య నువ్వు ఏం చేయగలవు?", False),
        ("assistant", "అరె sorry అండి.", True),
        ("assistant", "నేను ఒక AI-ని అండి, మీకు ఏమైనా help కావాలంటే చెప్తా.", True),
        ("assistant", "సరే.", False),
    )
    assert said(tidy(ctx)) == [
        ("system", "You are Nivya."),
        ("user", "నివ్య నువ్వు ఏం చేయగలవు?"),
        ("assistant", "నేను ఒక AI-ని అండి, మీకు ఏమైనా help కావాలంటే చెప్తా."),
        ("assistant", "సరే."),
    ]
    assert len(ctx.items) == 5


def test_a_long_call_is_read_from_its_latest_turns_with_her_instructions_kept() -> None:
    ctx = call(*((("user", f"question {n}", False)) for n in range(40)))
    kept = tidy(ctx)
    assert len(kept.items) == HISTORY_ITEMS + 1
    assert instructions_of(kept) == "You are Nivya."
    assert said(kept)[-1] == ("user", "question 39")


def test_a_reply_that_opens_like_one_of_her_last_three_is_a_repeat() -> None:
    earlier = replies(call(("assistant", CLASS_ANSWER, False), ("assistant", STORY_ONE, False)))
    assert repeats(CLASS_ANSWER, earlier)
    assert repeats("Sure, here it goes. There was once a king who loved", earlier)
    assert not repeats(STORY_TWO, earlier)
    assert not repeats("అరె sorry అండి.", earlier)


async def stream(*chunks: str) -> AsyncIterator[str]:
    for chunk in chunks:
        yield chunk


def spoken(chunks: AsyncIterable[str | object]) -> str:
    async def collect() -> str:
        return "".join([c async for c in chunks if isinstance(c, str)])

    return asyncio.run(collect())


def test_a_repeated_reply_is_asked_for_again_once_before_anything_is_spoken() -> None:
    asked: list[bool] = []

    def again() -> AsyncIterator[str]:
        asked.append(True)
        return stream("మళ్ళీ ", "చెప్తారా?")

    halves = (CLASS_ANSWER[:30], CLASS_ANSWER[30:])
    assert spoken(unrepeated(stream(*halves), again, [CLASS_ANSWER])) == "మళ్ళీ చెప్తారా?"
    assert asked == [True]


def test_a_new_reply_is_spoken_whole_and_never_asked_for_again() -> None:
    def again() -> AsyncIterator[str]:
        raise AssertionError("a new reply is never regenerated")

    chunks = ("Sure, here's ", "another one. ", "A clever fox ", "lived near a big river.")
    assert spoken(unrepeated(stream(*chunks), again, [STORY_ONE])) == "".join(chunks)
    assert spoken(unrepeated(stream("సరే."), again, ["సరే."])) == "సరే."


def went_quiet_then_called_again() -> llm.ChatContext:
    ctx = call(
        ("user", "[Speaker 1, to you] Nivya, that is all, be quiet now", False),
        ("assistant", "Okay, talk later.", False),
    )
    ctx.items.append(llm.FunctionCall(call_id="call_1", name="go_quiet", arguments="{}"))
    ctx.items.append(
        llm.FunctionCallOutput(call_id="call_1", name="go_quiet", output="", is_error=False)
    )
    ctx.add_message(role="user", content="[Speaker 1, to you] Nivya, are you there?")
    return ctx


def test_a_tool_that_ended_the_turn_is_sent_with_an_answer_sarvam_accepts() -> None:
    kept = tidy(went_quiet_then_called_again())
    [answer] = [item for item in kept.items if isinstance(item, llm.FunctionCallOutput)]
    assert answer.output == "done"
    assert said(kept)[-1] == ("user", "[Speaker 1, to you] Nivya, are you there?")


def test_a_call_without_its_answer_an_answer_without_its_call_and_empty_turns_are_left_out() -> (
    None
):
    ctx = call(("user", "Nivya, what time is it?", False), ("assistant", "  ", False))
    ctx.items.append(llm.FunctionCall(call_id="lost", name="current_time", arguments="{}"))
    ctx.items.append(
        llm.FunctionCallOutput(
            call_id="orphan", name="current_time", output="10:30", is_error=False
        )
    )
    kept = well_formed(ctx)
    assert [item.type for item in kept.items] == ["message", "message"]
    assert said(kept) == [("system", "You are Nivya."), ("user", "Nivya, what time is it?")]


def test_the_last_turn_alone_is_her_instructions_and_the_latest_question() -> None:
    minimal = last_turn(went_quiet_then_called_again())
    assert said(minimal) == [
        ("system", "You are Nivya."),
        ("user", "[Speaker 1, to you] Nivya, are you there?"),
    ]


def refused() -> APIStatusError:
    return APIStatusError("tool content empty", status_code=400, retryable=False)


async def failing(after: int) -> AsyncIterator[str]:
    for n in range(after):
        yield f"part {n} "
    raise refused()


def test_a_refused_request_is_asked_again_with_only_the_last_turn() -> None:
    assert (
        spoken(recovering(failing(0), lambda: stream("Yes, ", "I am here."))) == "Yes, I am here."
    )


def test_a_reply_that_fails_after_it_started_is_not_asked_again() -> None:
    def again() -> AsyncIterator[str]:
        raise AssertionError("a started reply is never sent again")

    with pytest.raises(APIStatusError):
        spoken(recovering(failing(1), again))
