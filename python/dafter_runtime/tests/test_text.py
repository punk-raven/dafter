from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from dafter_core.enums import EventType
from dafter_core.events import EventEnvelope, parse_event
from dafter_runtime.answering import Roster
from dafter_runtime.events import SessionEvents
from dafter_runtime.metrics import WorkerMetrics
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.toolbox import Answering, registry_for
from dafter_runtime.worker import watch
from livekit.agents import Agent, AgentSession, RunResult
from opentelemetry import trace
from prometheus_client import CollectorRegistry
from stub_llm import COMPLETION_TOKENS, PROMPT_TOKENS, REPLY, StubLLM, said

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"


def hindi_plan() -> Plan:
    return plan(load(JOB.read_bytes().strip()), "dafter-py")


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
    registry = CollectorRegistry()

    async def publish(body: bytes) -> None:
        sent.append(body)

    async def run() -> None:
        events = SessionEvents(
            p.config, publish, clock=lambda: datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
        )
        session = AgentSession[None](llm=StubLLM())
        watch(session, p, events, trace.NoOpTracer(), WorkerMetrics(registry))
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

    place = {"language": "hi", "channel": "webrtc"}
    ttft = {
        "layer": "llm_node_ttft",
        **place,
        "stt": "sarvam/saaras:v3-realtime",
        "llm": "sarvam/sarvam-105b",
        "tts": "sarvam/bulbul:v3",
    }
    assert registry.get_sample_value("dafter_agent_turn_layer_seconds_count", ttft) == 2
    assert registry.get_sample_value("dafter_agent_session_cost_inr_count", place) == 1
    llm_spend = {**place, "stage": "llm", "provider": "sarvam", "model": "sarvam-105b"}
    spent = registry.get_sample_value("dafter_agent_cost_inr_total", llm_spend)
    assert spent == pytest.approx(final["costInr"])


def of(events: list[EventEnvelope], event_type: EventType) -> list[dict[str, Any]]:
    return [e.payload for e in events if e.type is event_type]


def test_an_agent_that_always_answers_offers_the_everyday_tools_but_not_go_quiet() -> None:
    p = hindi_plan()
    stub = StubLLM()

    async def run() -> None:
        async with AgentSession[None](llm=stub) as session:
            registry = registry_for(p, session, Roster(), lambda: None, None)
            await session.start(Answering(p.persona.instructions, registry, lambda: None))
            await session.run(user_input="नमस्ते")

    asyncio.run(run())
    assert stub.offered == [["current_time", "who_is_here"]]
