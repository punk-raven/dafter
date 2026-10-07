from __future__ import annotations

import logging
from collections.abc import AsyncIterable, AsyncIterator, Callable
from difflib import SequenceMatcher

from livekit.agents import APIStatusError, FlushSentinel
from livekit.agents import llm as lk_llm

from .answering import TO_THE_ROOM
from .naming import words

log = logging.getLogger("dafter.runtime.history")

CUT_SHORT_WORDS = 5
RECENT_REPLIES = 3
OPENING_WORDS = 8
REPEAT_RATIO = 0.8
REPEATED = (
    "You were about to say again something you already said in this call. Do not repeat "
    "it. If you are not sure what they mean, ask them in a few words to say it again."
)

STOPPED_TOOL = "done"

Chunk = lk_llm.ChatChunk | str | FlushSentinel


def well_formed(chat_ctx: lk_llm.ChatContext) -> lk_llm.ChatContext:
    called = {item.call_id for item in chat_ctx.items if isinstance(item, lk_llm.FunctionCall)}
    answered = {
        item.call_id for item in chat_ctx.items if isinstance(item, lk_llm.FunctionCallOutput)
    }
    kept: list[lk_llm.ChatItem] = []
    for item in chat_ctx.items:
        if isinstance(item, lk_llm.FunctionCall) and item.call_id not in answered:
            continue
        if isinstance(item, lk_llm.FunctionCallOutput):
            if item.call_id not in called:
                continue
            if not item.output.strip():
                item = item.model_copy(update={"output": STOPPED_TOOL})
        if (
            isinstance(item, lk_llm.ChatMessage)
            and item.role in ("user", "assistant")
            and not (item.text_content or "").strip()
        ):
            continue
        kept.append(item)
    chat_ctx.items[:] = kept
    return chat_ctx


def judged(chat_ctx: lk_llm.ChatContext) -> bool:
    asked = [
        item
        for item in chat_ctx.items
        if isinstance(item, lk_llm.ChatMessage) and item.role == "user"
    ]
    if not asked:
        return False
    lines = (asked[-1].text_content or "").strip().splitlines()
    return bool(lines) and f", {TO_THE_ROOM}]" in lines[-1]


def last_turn(chat_ctx: lk_llm.ChatContext) -> lk_llm.ChatContext:
    minimal = lk_llm.ChatContext()
    asked = [
        item
        for item in chat_ctx.items
        if isinstance(item, lk_llm.ChatMessage) and (item.text_content or "").strip()
    ]
    instructions = [m for m in asked if m.role in ("system", "developer")][:1]
    users = [m for m in asked if m.role == "user"][-1:]
    minimal.items[:] = [*instructions, *users]
    return minimal


async def recovering(
    first: AsyncIterable[Chunk], fallback: Callable[[], AsyncIterable[Chunk]]
) -> AsyncIterator[Chunk]:
    started = False
    try:
        async for chunk in first:
            started = True
            yield chunk
    except APIStatusError as exc:
        if started or exc.retryable:
            raise
        log.warning(
            "the model refused the request, it is asked again with only the last turn",
            extra={"status": exc.status_code},
        )
        async for chunk in fallback():
            yield chunk


def cut_short(item: lk_llm.ChatItem) -> bool:
    if not isinstance(item, lk_llm.ChatMessage) or item.role != "assistant":
        return False
    return item.interrupted and len(words(item.text_content or "")) < CUT_SHORT_WORDS


def replies(chat_ctx: lk_llm.ChatContext, count: int = RECENT_REPLIES) -> list[str]:
    said = [
        item.text_content or ""
        for item in chat_ctx.items
        if isinstance(item, lk_llm.ChatMessage) and item.role == "assistant"
    ]
    return [text for text in said if text][-count:]


def repeats(opening: str, earlier: list[str]) -> bool:
    heard = words(opening)[:OPENING_WORDS]
    if len(heard) < OPENING_WORDS:
        return False
    for reply in earlier:
        before = words(reply)[: len(heard)]
        if SequenceMatcher(None, heard, before).ratio() >= REPEAT_RATIO:
            return True
    return False


def text_of(chunk: Chunk) -> str:
    if isinstance(chunk, str):
        return chunk
    if isinstance(chunk, lk_llm.ChatChunk) and chunk.delta is not None:
        return chunk.delta.content or ""
    return ""


def opened(text: str) -> bool:
    return len(words(text)) >= OPENING_WORDS


async def unrepeated(
    first: AsyncIterable[Chunk],
    again: Callable[[], AsyncIterable[Chunk]],
    earlier: list[str],
) -> AsyncIterator[Chunk]:
    stream = aiter(first)
    held: list[Chunk] = []
    text = ""
    async for chunk in stream:
        held.append(chunk)
        text += text_of(chunk)
        if opened(text):
            break
    if repeats(text, earlier):
        log.info("a reply that repeated an earlier one is asked for again")
        closing = getattr(stream, "aclose", None)
        if closing is not None:
            await closing()
        async for chunk in again():
            yield chunk
        return
    for chunk in held:
        yield chunk
    async for chunk in stream:
        yield chunk


def instructions_of(chat_ctx: lk_llm.ChatContext) -> str:
    return next(
        (
            item.text_content or ""
            for item in chat_ctx.items
            if isinstance(item, lk_llm.ChatMessage) and item.role in ("system", "developer")
        ),
        "",
    )


__all__ = [
    "REPEATED",
    "cut_short",
    "instructions_of",
    "judged",
    "last_turn",
    "recovering",
    "repeats",
    "replies",
    "unrepeated",
    "well_formed",
]
