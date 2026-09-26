from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dafter_core.enums import EventType
from dafter_core.events import EventEnvelope, parse_event
from dafter_runtime.events import SessionEvents
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.worker import watch
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    Agent,
    AgentSession,
    APIConnectOptions,
    RunResult,
    llm,
)
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from opentelemetry import trace

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
REPLY = "नमस्ते! बताइए, मैं क्या मदद करूँ?"
PROMPT_TOKENS = 50
COMPLETION_TOKENS = 7


class StubStream(llm.LLMStream):
    def __init__(
        self,
        owner: StubLLM,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(owner, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._reply = owner.reply

    async def _run(self) -> None:
        delta = llm.ChoiceDelta(role="assistant", content=self._reply)
        usage = llm.CompletionUsage(
            completion_tokens=COMPLETION_TOKENS,
            prompt_tokens=PROMPT_TOKENS,
            total_tokens=PROMPT_TOKENS + COMPLETION_TOKENS,
        )
        self._event_ch.send_nowait(llm.ChatChunk(id="stub", delta=delta))
        self._event_ch.send_nowait(llm.ChatChunk(id="stub", usage=usage))


class StubLLM(llm.LLM[Any]):
    def __init__(self, reply: str = REPLY) -> None:
        super().__init__()
        self.reply = reply
        self.requests: list[llm.ChatContext] = []

    @property
    def model(self) -> str:
        return "sarvam-105b"

    @property
    def provider(self) -> str:
        return "Sarvam"

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
        self.requests.append(chat_ctx.copy())
        return StubStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


def hindi_plan() -> Plan:
    return plan(load(JOB.read_bytes().strip()), "dafter-py")


def said(ctx: llm.ChatContext) -> list[tuple[str, str | None]]:
    return [(m.role, m.text_content) for m in ctx.items if isinstance(m, llm.ChatMessage)]


def test_the_persona_answers_a_text_turn() -> None:
    p = hindi_plan()
    stub = StubLLM()

    async def run() -> None:
        async with AgentSession[None](llm=stub) as session:
            await session.start(Agent(instructions=p.persona.instructions))
            result: RunResult[None] = await session.run(user_input="नमस्ते")
            reply = result.expect.next_event().is_message(role="assistant")
            result.expect.no_more_events()
            assert reply.event().item.text_content == REPLY

    asyncio.run(run())
    [request] = stub.requests
    assert said(request) == [("system", p.persona.instructions), ("user", "नमस्ते")]


def test_a_second_turn_carries_the_conversation() -> None:
    stub = StubLLM()

    async def run() -> None:
        async with AgentSession[None](llm=stub) as session:
            await session.start(Agent(instructions=hindi_plan().persona.instructions))
            await session.run(user_input="नमस्ते")
            result: RunResult[None] = await session.run(user_input="आज मौसम कैसा है?")
            result.expect.next_event().is_message(role="assistant")

    asyncio.run(run())
    assert said(stub.requests[1])[1:] == [
        ("user", "नमस्ते"),
        ("assistant", REPLY),
        ("user", "आज मौसम कैसा है?"),
    ]


def test_a_text_session_reports_its_turns_and_what_they_cost() -> None:
    p = hindi_plan()
    sent: list[bytes] = []

    async def publish(body: bytes) -> None:
        sent.append(body)

    async def run() -> None:
        events = SessionEvents(
            p.config, publish, clock=lambda: datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
        )
        session = AgentSession[None](llm=StubLLM())
        watch(session, p, events, trace.NoOpTracer())
        await session.start(Agent(instructions=p.persona.instructions))
        await session.run(user_input="नमस्ते")
        await session.run(user_input="धन्यवाद")
        await session.aclose()
        await events.drain()

    asyncio.run(run())
    parsed = [parse_event(body) for body in sent]
    assert [e.sequence for e in parsed] == list(range(len(parsed)))

    turns = of(parsed, EventType.AGENT_TURN_METRICS)
    assert [(t["turn"], t["interrupted"]) for t in turns] == [(0, False), (1, False)]
    assert all("llmNodeTtftMs" in t for t in turns)

    usage = of(parsed, EventType.SESSION_USAGE)
    assert [u["final"] for u in usage] == [False, False, True]
    final = usage[-1]
    assert final["unpricedItems"] == 0
    by_unit = {i["unit"]: i for i in final["items"]}
    assert by_unit["input_token"]["quantity"] == 2 * PROMPT_TOKENS
    assert by_unit["output_token"]["quantity"] == 2 * COMPLETION_TOKENS
    assert final["costInr"] == round(
        2 * PROMPT_TOKENS * 29.28 / 1e6 + 2 * COMPLETION_TOKENS * 73.20 / 1e6, 6
    )
    assert json.loads(sent[-1])["type"] == "session.usage"


def of(events: list[EventEnvelope], event_type: EventType) -> list[dict[str, Any]]:
    return [e.payload for e in events if e.type is event_type]
