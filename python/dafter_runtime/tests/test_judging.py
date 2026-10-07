from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from dafter_runtime.judging import decided, verdict_prompt
from livekit.agents import llm


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
