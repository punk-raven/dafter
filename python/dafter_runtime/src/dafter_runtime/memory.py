from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from livekit.agents import llm as lk_llm
from livekit.agents.voice.generation import update_instructions

from .everyday import STAY_SILENT
from .history import cut_short, instructions_of, well_formed

log = logging.getLogger("dafter.runtime.memory")

VERBATIM_TOKENS = 8000
HARD_TOKENS = 24000
CHARS_PER_TOKEN = 3
FOLD_SHARE = 0.5
SUMMARY_WORDS = 350
EARLIER = "What was said earlier in this call, summarised:"
SUMMARISE = (
    "You keep the running minutes of a live call. Merge the earlier minutes and the new part "
    "of the transcript into one set of minutes. Keep who said what, by name, and every fact, "
    "number, date, place, decision, question and open request; keep the speakers' own words "
    "for anything specific. Plain sentences, no lists or headings, under "
    f"{SUMMARY_WORDS} words, in the language most of the call is in."
)

Summarise = Callable[[str], Awaitable[str]]


def is_instructions(item: lk_llm.ChatItem) -> bool:
    return isinstance(item, lk_llm.ChatMessage) and item.role in ("system", "developer")


def silent(item: lk_llm.ChatItem) -> bool:
    if isinstance(item, (lk_llm.FunctionCall, lk_llm.FunctionCallOutput)):
        return item.name == STAY_SILENT
    return False


def tokens(item: lk_llm.ChatItem) -> int:
    if isinstance(item, lk_llm.ChatMessage):
        text = item.text_content or ""
    elif isinstance(item, lk_llm.FunctionCall):
        text = item.name + item.arguments
    elif isinstance(item, lk_llm.FunctionCallOutput):
        text = item.output
    else:
        text = ""
    return len(text) // CHARS_PER_TOKEN + 4


def minutes_of(items: list[lk_llm.ChatItem], agent: str) -> str:
    lines = []
    for item in items:
        if not isinstance(item, lk_llm.ChatMessage) or not (item.text_content or "").strip():
            continue
        text = (item.text_content or "").strip()
        lines.append(f"[{agent}] {text}" if item.role == "assistant" else text)
    return "\n".join(lines)


async def summarised(model: lk_llm.LLM[Any], prompt: str) -> str:
    ctx = lk_llm.ChatContext()
    ctx.add_message(role="system", content=SUMMARISE)
    ctx.add_message(role="user", content=prompt)
    said = ""
    async with model.chat(chat_ctx=ctx) as stream:
        async for chunk in stream:
            if chunk.delta is not None and chunk.delta.content:
                said += chunk.delta.content
    return said.strip()


class Memory:
    def __init__(self, summarise: Summarise | None, agent: str = "You") -> None:
        self._summarise = summarise
        self._agent = agent
        self.summary = ""
        self._folded: set[str] = set()
        self._folding: asyncio.Task[None] | None = None

    def context(self, chat_ctx: lk_llm.ChatContext) -> lk_llm.ChatContext:
        kept = chat_ctx.copy()
        items = [
            item
            for item in kept.items
            if not cut_short(item) and not silent(item) and item.id not in self._folded
        ]
        head = [item for item in items if is_instructions(item)][:1]
        body = [item for item in items if not is_instructions(item)]
        size = sum(tokens(item) for item in body)
        if size > VERBATIM_TOKENS:
            self._fold(body, size)
        dropped = 0
        while size > HARD_TOKENS and len(body) > 1:
            size -= tokens(body.pop(0))
            dropped += 1
        if dropped:
            log.warning("the oldest turns did not fit and were left out", extra={"turns": dropped})
        kept.items[:] = [*head, *body]
        if self.summary:
            remembered = f"{instructions_of(kept)}\n\n{EARLIER}\n{self.summary}"
            update_instructions(kept, instructions=remembered, add_if_missing=True)
        return well_formed(kept)

    def _fold(self, body: list[lk_llm.ChatItem], size: int) -> None:
        if self._summarise is None or (self._folding is not None and not self._folding.done()):
            return
        oldest: list[lk_llm.ChatItem] = []
        taken = 0
        for item in body[:-1]:
            if taken >= size * FOLD_SHARE:
                break
            oldest.append(item)
            taken += tokens(item)
        if oldest:
            self._folding = asyncio.ensure_future(self._folded_into_summary(oldest))

    async def _folded_into_summary(self, oldest: list[lk_llm.ChatItem]) -> None:
        if self._summarise is None:
            return
        earlier = f"Earlier minutes:\n{self.summary}\n\n" if self.summary else ""
        prompt = f"{earlier}New part of the transcript:\n{minutes_of(oldest, self._agent)}"
        try:
            summary = await self._summarise(prompt)
        except Exception as exc:
            log.warning(
                "the call could not be summarised, it is tried again later",
                extra={"error": type(exc).__name__},
            )
            return
        if not summary:
            return
        self.summary = summary
        self._folded.update(item.id for item in oldest)
        log.info("earlier turns were folded into the summary", extra={"turns": len(oldest)})


__all__ = ["EARLIER", "Memory", "minutes_of", "silent", "summarised", "tokens"]
