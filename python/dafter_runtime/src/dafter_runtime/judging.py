from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterable, AsyncIterator, Awaitable
from typing import Any

from livekit.agents import llm as lk_llm

from .history import Chunk
from .memory import is_instructions, minutes_of

log = logging.getLogger("dafter.runtime.judging")

RECENT_ITEMS = 24
UNMARKED = re.compile(r", (?:to you|to the room|not to you)\]")
VERDICT_TOKENS = 3
VERDICT_SECONDS = 4.0
DECIDE = (
    "You listen to a live group call between people and an assistant called {name}. "
    "{name} has been invited into the conversation and is listening. Decide whether {name} "
    "should answer the LAST line of the transcript. Reply with one word: YES or NO.\n"
    "Answer YES when the last line is a question or request that the assistant can answer and "
    "that is not directed at a specific other person: a follow-up to what {name} just said, a "
    "general question such as about facts, the time, the weather or what was said, or a "
    "request for a joke, a story or help. When {name} spoke just before and the last line is a "
    "question that does not name another person, it is a follow-up: answer YES.\n"
    "Answer NO when the last line is said to another person (for example it names them, or "
    "it answers or reacts to what another person said), or it is a statement, plan or chat "
    "between the people that does not ask the assistant anything.\n"
    "When it is truly unclear, answer NO."
)


def spoke_just_before(recent: list[lk_llm.ChatItem]) -> bool:
    said = [item for item in recent if isinstance(item, lk_llm.ChatMessage)]
    if len(said) < 2:
        return False
    last, before = said[-1], said[-2]
    alone = len((last.text_content or "").strip().splitlines()) == 1
    return alone and last.role == "user" and before.role == "assistant"


def verdict_prompt(chat_ctx: lk_llm.ChatContext, name: str) -> lk_llm.ChatContext:
    name = name or "the assistant"
    recent = [item for item in chat_ctx.items if not is_instructions(item)][-RECENT_ITEMS:]
    ctx = lk_llm.ChatContext()
    ctx.add_message(role="system", content=DECIDE.format(name=name))
    transcript = UNMARKED.sub("]", minutes_of(recent, f"{name} (assistant)"))
    before = "spoke" if spoke_just_before(recent) else "did not speak"
    ask = (
        f"Transcript:\n{transcript}\n\n{name} {before} just before the last line. "
        f"Should {name} answer the last line?"
    )
    ctx.add_message(role="user", content=ask)
    return ctx


async def meant_for_her(model: lk_llm.LLM[Any], chat_ctx: lk_llm.ChatContext, name: str) -> bool:
    started = time.perf_counter()
    said = ""
    try:
        extra = {"max_tokens": VERDICT_TOKENS, "temperature": 0}
        async with asyncio.timeout(VERDICT_SECONDS):
            async with model.chat(
                chat_ctx=verdict_prompt(chat_ctx, name), extra_kwargs=extra
            ) as stream:
                async for chunk in stream:
                    if chunk.delta is not None and chunk.delta.content:
                        said += chunk.delta.content
    except Exception as exc:
        log.warning(
            "a line could not be judged, it is left unanswered", extra={"error": type(exc).__name__}
        )
        return False
    meant = said.strip().upper().startswith("YES")
    log.info(
        "a line to the room was judged",
        extra={"meant_for_her": meant, "judged_ms": round((time.perf_counter() - started) * 1000)},
    )
    return meant


async def decided(first: AsyncIterable[Chunk], decision: Awaitable[bool]) -> AsyncIterator[Chunk]:
    if not await decision:
        return
    async for chunk in first:
        yield chunk


__all__ = ["DECIDE", "decided", "meant_for_her", "spoke_just_before", "verdict_prompt"]
