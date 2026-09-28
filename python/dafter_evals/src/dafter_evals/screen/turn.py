from __future__ import annotations

import re
import time
from collections.abc import AsyncIterable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from dafter_core.enums import Stage
from dafter_core.errors import DafterError
from livekit.agents import (
    Agent,
    AgentSession,
    APIConnectOptions,
    FlushSentinel,
    ModelSettings,
    RunResult,
    llm,
)
from livekit.agents.metrics import LLMModelUsage
from livekit.agents.voice.agent_session import SessionConnectOptions

SENTENCE_END = re.compile(r"[.!?।॥]")

Classify = Callable[[BaseException, Stage], DafterError]
Now = Callable[[], float]


@dataclass
class Clock:
    now: Now = time.perf_counter
    start: float | None = None
    first_token: float | None = None
    first_sentence: float | None = None
    end: float | None = None
    text: str = ""

    def started(self) -> None:
        if self.start is None:
            self.start = self.now()

    def saw(self, piece: str) -> None:
        at = self.now()
        if self.first_token is None and piece.strip():
            self.first_token = at
        self.text += piece
        if self.first_sentence is None and SENTENCE_END.search(self.text):
            self.first_sentence = at

    def finished(self) -> None:
        self.end = self.now()
        if self.first_sentence is None and self.text.strip():
            self.first_sentence = self.end

    def ms(self, at: float | None) -> int | None:
        if at is None or self.start is None:
            return None
        return round((at - self.start) * 1000)


class TimedAgent(Agent):
    def __init__(self, instructions: str, clock: Clock, tools: Sequence[llm.Tool]) -> None:
        super().__init__(instructions=instructions, tools=list(tools))
        self.clock = clock

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        model_settings: ModelSettings,
    ) -> AsyncIterable[llm.ChatChunk | str | FlushSentinel]:
        self.clock.started()
        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            if isinstance(chunk, llm.ChatChunk) and chunk.delta and chunk.delta.content:
                self.clock.saw(chunk.delta.content)
            elif isinstance(chunk, str) and chunk:
                self.clock.saw(chunk)
            yield chunk
        self.clock.finished()


@dataclass(frozen=True, slots=True)
class Reply:
    text: str | None
    ttft_ms: int | None
    ttfs_ms: int | None
    total_ms: int | None
    input_tokens: int
    output_tokens: int
    tool_calls: int
    error: DafterError | None
    reasoning_tokens: int = 0
    history: llm.ChatContext | None = field(default=None, compare=False)


def _tokens(session: AgentSession[Any]) -> tuple[int, int, int]:
    used = [u for u in session.usage.model_usage if isinstance(u, LLMModelUsage)]
    return (
        sum(u.input_tokens for u in used),
        sum(u.output_tokens for u in used),
        sum(u.output_reasoning_tokens for u in used),
    )


async def ask(
    model: llm.LLM[Any],
    classify: Classify,
    instructions: str,
    question: str,
    timeout: float,
    now: Now = time.perf_counter,
    tools: list[llm.Tool] | None = None,
) -> Reply:
    clock = Clock(now=now)
    options = SessionConnectOptions(
        llm_conn_options=APIConnectOptions(max_retry=0, timeout=timeout)
    )
    session: AgentSession[None] = AgentSession(llm=model, conn_options=options)
    try:
        async with session:
            await session.start(TimedAgent(instructions, clock, tools or []))
            result: RunResult[None] = await session.run(user_input=question)
            tokens = _tokens(session)
            history = session.history.copy()
    except Exception as exc:
        return Reply(
            None, clock.ms(clock.first_token), None, None, 0, 0, 0, classify(exc, Stage.LLM)
        )
    said = [
        e.item.text_content or ""
        for e in result.events
        if e.type == "message" and e.item.role == "assistant"
    ]
    return Reply(
        text=" ".join(s for s in said if s) or None,
        ttft_ms=clock.ms(clock.first_token),
        ttfs_ms=clock.ms(clock.first_sentence),
        total_ms=clock.ms(clock.end),
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        tool_calls=sum(1 for e in result.events if e.type == "function_call"),
        error=None,
        reasoning_tokens=tokens[2],
        history=history,
    )
