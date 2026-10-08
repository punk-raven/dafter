from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from dafter_runtime.judging import (
    decided,
    meant_for_her,
    spoke_just_before,
    verdict_of,
    verdict_prompt,
)
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions, llm
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from stub_llm import StubLLM


async def reply(*chunks: str) -> AsyncIterator[str]:
    for chunk in chunks:
        await asyncio.sleep(0)
        yield chunk


async def verdict(meant: bool) -> bool:
    await asyncio.sleep(0.01)
    return meant


def spoken(meant: bool) -> list[str]:
    async def run() -> list[str]:
        chunks = decided(reply("Meera and ", "Kiran are coming."), verdict(meant))
        return [c async for c in chunks if isinstance(c, str)]

    return asyncio.run(run())


def test_a_line_judged_for_her_is_answered_with_the_reply_made_while_judging() -> None:
    assert spoken(True) == ["Meera and ", "Kiran are coming."]


def test_a_line_judged_not_for_her_gets_no_words_at_all() -> None:
    assert spoken(False) == []


def test_the_judge_reads_plain_speaker_names_and_the_assistant_by_name() -> None:
    ctx = llm.ChatContext()
    ctx.add_message(role="system", content="You are Nivya.")
    ctx.add_message(role="user", content="[Asha, to you] Nivya, who is coming?")
    ctx.add_message(role="assistant", content="Meera and Kiran.")
    ctx.add_message(role="user", content="[Ravi, not to you] ok\n[Asha, to the room] and when?")
    judge = verdict_prompt(ctx, "Nivya")
    [system, asked] = [item for item in judge.items if isinstance(item, llm.ChatMessage)]
    assert "assistant called Nivya" in (system.text_content or "")
    assert (asked.text_content or "").splitlines()[1:5] == [
        "[Asha] Nivya, who is coming?",
        "[Nivya (assistant)] Meera and Kiran.",
        "[Ravi] ok",
        "[Asha] and when?",
    ]
    assert "Nivya did not speak just before the last line." in (asked.text_content or "")


def test_a_question_right_after_her_answer_is_marked_as_following_her() -> None:
    ctx = llm.ChatContext()
    ctx.add_message(role="assistant", content="Try Palolem beach.")
    ctx.add_message(role="user", content="[Asha, to the room] is it far from the hotel?")
    asked = verdict_prompt(ctx, "Nivya").items[-1]
    assert isinstance(asked, llm.ChatMessage)
    assert "Nivya spoke just before the last line." in (asked.text_content or "")


@pytest.mark.parametrize(
    ("said", "meant"),
    [
        ("YES", True),
        ("**YES**", True),
        ("yes.", True),
        ('"Yes"', True),
        ("Yes, it is a follow-up", True),
        ("NO", False),
        ("no", False),
        ("**No**.", False),
        ("", None),
        ("Maybe", None),
        ("YESTERDAY", None),
    ],
)
def test_the_verdict_is_the_first_word_whatever_wraps_it(said: str, meant: bool | None) -> None:
    assert verdict_of(said) is meant


def test_she_spoke_just_before_when_only_lines_judged_not_for_her_came_after() -> None:
    ctx = llm.ChatContext()
    ctx.add_message(role="user", content="[Asha, to you] Nivya, ఏం చేద్దాం?")
    ctx.add_message(role="assistant", content="ఏంటి plan?")
    ctx.add_message(role="user", content="[Asha, to the room] Goa veldam")
    ctx.add_message(role="user", content="[Asha, to the room] Saturday")
    assert spoke_just_before(list(ctx.items))
    ctx.add_message(role="user", content="[Ravi, to you] Nivya, wait")
    ctx.add_message(role="user", content="[Asha, to the room] and Sunday?")
    assert not spoke_just_before(list(ctx.items))


def test_the_judge_is_told_who_is_present_and_what_she_last_said() -> None:
    ctx = llm.ChatContext()
    ctx.add_message(role="assistant", content="ఏంటి plan?")
    ctx.add_message(role="user", content="[Asha, to the room] Goa veldam")
    asked = verdict_prompt(ctx, "Nivya", 1).items[-1]
    assert isinstance(asked, llm.ChatMessage)
    text = asked.text_content or ""
    assert "1 person is in the call besides Nivya." in text
    assert "The last line Nivya said was: ఏంటి plan? (a question)." in text
    plain = verdict_prompt(llm.ChatContext(), "Nivya", 3).items[-1]
    assert isinstance(plain, llm.ChatMessage)
    assert "3 people are in the call besides Nivya. Nivya has not spoken yet." in (
        plain.text_content or ""
    )


class Bench:
    def __init__(self) -> None:
        self.verdicts: list[bool] = []

    def people(self) -> int:
        return 2

    def verdict(self, meant: bool) -> None:
        self.verdicts.append(meant)


class Limits(StubLLM):
    def __init__(self, verdicts: list[str]) -> None:
        super().__init__(verdicts=verdicts)
        self.extra: list[object] = []

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> llm.LLMStream:
        self.extra.append(extra_kwargs)
        return super().chat(chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)


def judge(said: str) -> tuple[bool, Bench, Limits]:
    model, bench = Limits([said]), Bench()
    ctx = llm.ChatContext()
    ctx.add_message(role="assistant", content="Try Palolem beach.")
    ctx.add_message(role="user", content="[Asha, to the room] is it far?")
    meant = asyncio.run(meant_for_her(model, ctx, "Nivya", bench))
    return meant, bench, model


def test_the_verdict_reaches_the_gate_and_sends_only_the_routes_own_token_limit() -> None:
    meant, bench, model = judge("**YES**")
    assert meant
    assert bench.verdicts == [True]
    assert model.extra == [NOT_GIVEN]
    asked = model.judged[0].items[-1]
    assert isinstance(asked, llm.ChatMessage)
    assert "2 people are in the call besides Nivya." in (asked.text_content or "")


def test_an_answer_that_is_neither_yes_nor_no_is_a_no_the_gate_hears_of() -> None:
    meant, bench, _ = judge("Probably")
    assert not meant
    assert bench.verdicts == [False]
