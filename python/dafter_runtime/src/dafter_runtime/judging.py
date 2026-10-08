from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterable, AsyncIterator, Awaitable
from typing import Any, Protocol

from livekit.agents import APIStatusError
from livekit.agents import llm as lk_llm

from .answering import TO_THE_ROOM
from .backchannel import is_question
from .history import Chunk
from .memory import is_instructions, minutes_of

log = logging.getLogger("dafter.runtime.judging")

RECENT_ITEMS = 24
UNMARKED = re.compile(r", (?:to you|to the room|not to you)\]")
FIRST_WORD = re.compile(r"[^A-Za-z]*([A-Za-z]+)")
WORD_DONE = re.compile(r"[^A-Za-z]*[A-Za-z]+[^A-Za-z]")
VERDICTS = {"YES": True, "NO": False}
VERDICT_SECONDS = 4.0
RAW_CHARS = 16
DECIDE = (
    "You listen to a live group call between people and an assistant called {name}. "
    "{name} has been invited into the conversation and is listening. Decide whether {name} "
    "should answer the LAST line of the transcript. Reply with one word: YES or NO.\n"
    "Answer YES when the last line answers a question {name} asked, or follows up on what "
    "{name} just said, or is a question or request that the assistant can answer and that is "
    "not directed at a specific other person: a general question such as about facts, the "
    "time, the weather or what was said, or a request for a joke, a story or help. When "
    "{name} spoke just before and the last line is a question that does not name another "
    "person, it is a follow-up: answer YES. When only one person is in the call, a line "
    "with words is for {name} unless it is clearly said to someone else.\n"
    "Answer NO when the last line is said to another person (for example it names them, or "
    "it answers or reacts to what another person said), or it is a statement, plan or chat "
    "between the people that does not ask the assistant anything.\n"
    "When it is truly unclear, answer NO."
)


class Bench(Protocol):
    def people(self) -> int: ...

    def verdict(self, meant: bool) -> None: ...


def refused(item: lk_llm.ChatMessage) -> bool:
    lines = (item.text_content or "").strip().splitlines()
    return item.role == "user" and bool(lines) and f", {TO_THE_ROOM}]" in lines[-1]


def spoke_just_before(recent: list[lk_llm.ChatItem]) -> bool:
    said = [
        item
        for item in recent
        if isinstance(item, lk_llm.ChatMessage) and item.role in ("user", "assistant")
    ]
    if not said:
        return False
    last = said[-1]
    alone = len((last.text_content or "").strip().splitlines()) == 1
    if not alone or last.role != "user":
        return False
    for item in reversed(said[:-1]):
        if item.role == "assistant":
            return True
        if not refused(item):
            return False
    return False


def last_said(recent: list[lk_llm.ChatItem]) -> str | None:
    said = [
        item.text_content or ""
        for item in recent
        if isinstance(item, lk_llm.ChatMessage) and item.role == "assistant"
    ]
    return said[-1].strip() if said else None


def setting(recent: list[lk_llm.ChatItem], name: str, people: int | None) -> str:
    present = ""
    if people:
        present = "1 person is" if people == 1 else f"{people} people are"
        present = f"{present} in the call besides {name}. "
    last = last_said(recent)
    if last is None:
        return f"{present}{name} has not spoken yet. "
    kind = "a question" if is_question(last) else "not a question"
    return f"{present}The last line {name} said was: {last} ({kind}). "


def verdict_prompt(
    chat_ctx: lk_llm.ChatContext, name: str, people: int | None = None
) -> lk_llm.ChatContext:
    name = name or "the assistant"
    recent = [item for item in chat_ctx.items if not is_instructions(item)][-RECENT_ITEMS:]
    ctx = lk_llm.ChatContext()
    ctx.add_message(role="system", content=DECIDE.format(name=name))
    transcript = UNMARKED.sub("]", minutes_of(recent, f"{name} (assistant)"))
    before = "spoke" if spoke_just_before(recent) else "did not speak"
    ask = (
        f"Transcript:\n{transcript}\n\n{setting(recent, name, people)}"
        f"{name} {before} just before the last line. Should {name} answer the last line?"
    )
    ctx.add_message(role="user", content=ask)
    return ctx


def verdict_of(said: str) -> bool | None:
    first = FIRST_WORD.match(said)
    return VERDICTS.get(first.group(1).upper()) if first else None


def failure(exc: Exception) -> dict[str, Any]:
    if isinstance(exc, TimeoutError):
        return {"error": "TimeoutError", "reason": f"no verdict within {VERDICT_SECONDS} s"}
    if isinstance(exc, APIStatusError):
        return {"error": type(exc).__name__, "reason": f"status {exc.status_code}"}
    return {"error": type(exc).__name__, "reason": "the request failed"}


async def asked(model: lk_llm.LLM[Any], prompt: lk_llm.ChatContext) -> str:
    said = ""
    async with model.chat(chat_ctx=prompt) as stream:
        async for chunk in stream:
            if chunk.delta is not None and chunk.delta.content:
                said += chunk.delta.content
                if WORD_DONE.match(said):
                    break
    return said


async def meant_for_her(
    model: lk_llm.LLM[Any],
    chat_ctx: lk_llm.ChatContext,
    name: str,
    bench: Bench | None = None,
) -> bool:
    started = time.perf_counter()
    prompt = verdict_prompt(chat_ctx, name, bench.people() if bench is not None else None)
    try:
        async with asyncio.timeout(VERDICT_SECONDS):
            said = await asked(model, prompt)
    except Exception as exc:
        log.warning("a line could not be judged, it is left unanswered", extra=failure(exc))
        return judged(bench, False)
    raw = said.strip()[:RAW_CHARS]
    meant = verdict_of(said)
    if meant is None:
        log.warning(
            "a line could not be judged, it is left unanswered",
            extra={"error": "NoVerdict", "reason": "no YES or NO first", "raw": raw},
        )
        return judged(bench, False)
    log.info(
        "a line to the room was judged",
        extra={
            "meant_for_her": meant,
            "raw": raw,
            "judged_ms": round((time.perf_counter() - started) * 1000),
        },
    )
    return judged(bench, meant)


def judged(bench: Bench | None, meant: bool) -> bool:
    if bench is not None:
        bench.verdict(meant)
    return meant


async def decided(first: AsyncIterable[Chunk], decision: Awaitable[bool]) -> AsyncIterator[Chunk]:
    if not await decision:
        return
    async for chunk in first:
        yield chunk


__all__ = [
    "DECIDE",
    "Bench",
    "decided",
    "meant_for_her",
    "spoke_just_before",
    "verdict_of",
    "verdict_prompt",
]
