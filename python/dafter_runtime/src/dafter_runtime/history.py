from __future__ import annotations

import logging
from collections.abc import AsyncIterable, AsyncIterator, Callable
from difflib import SequenceMatcher

from livekit.agents import FlushSentinel
from livekit.agents import llm as lk_llm

from .naming import words

log = logging.getLogger("dafter.runtime.history")

HISTORY_ITEMS = 16
CUT_SHORT_WORDS = 5
RECENT_REPLIES = 3
OPENING_WORDS = 8
REPEAT_RATIO = 0.8
REPEATED = (
    "You were about to say again something you already said in this call. Do not repeat "
    "it. If you are not sure what they mean, ask them in a few words to say it again."
)

Chunk = lk_llm.ChatChunk | str | FlushSentinel


def tidy(chat_ctx: lk_llm.ChatContext) -> lk_llm.ChatContext:
    kept = chat_ctx.copy()
    kept.items[:] = [item for item in kept.items if not cut_short(item)]
    return kept.truncate(max_items=HISTORY_ITEMS)


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
    "repeats",
    "replies",
    "tidy",
    "unrepeated",
]
