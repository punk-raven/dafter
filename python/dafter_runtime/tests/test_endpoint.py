from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from dafter_core.enums import EventType
from dafter_core.events import parse_event
from dafter_providers.endpointing import Endpoint
from dafter_runtime.events import SessionEvents
from dafter_runtime.metrics import WorkerMetrics
from dafter_runtime.plan import load, plan
from dafter_runtime.timing import Turns
from dafter_runtime.worker import watch
from livekit.agents import AgentSession
from livekit.agents.llm import ChatMessage
from livekit.agents.metrics import AgentSessionUsage
from livekit.agents.voice.events import ConversationItemAddedEvent
from opentelemetry import trace
from prometheus_client import CollectorRegistry

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
STOPPED = 1_758_000_000.0


def message(role: str, metrics: dict[str, Any] | None = None) -> ChatMessage:
    return ChatMessage.model_validate({"role": role, "content": ["x"], "metrics": metrics or {}})


class Held:
    def __init__(self, *found: Endpoint | None) -> None:
        self.found = list(found)

    def take_endpoint(self) -> Endpoint | None:
        return self.found.pop(0) if self.found else None


USER = {"end_of_turn_delay": 0.003, "transcription_delay": 0.0}
REPLY = {"e2e_latency": 0.73, "started_speaking_at": STOPPED + 1.8}


def test_a_reply_carries_the_endpoint_and_the_gap_from_the_last_voiced_audio() -> None:
    turns = Turns(Held(Endpoint(voiced_until=STOPPED, released_at=STOPPED + 1.063)).take_endpoint)
    assert turns.add(message("user", USER)) is None
    timing = turns.add(message("assistant", REPLY))
    assert timing is not None
    assert timing.payload() == {
        "turn": 0,
        "interrupted": False,
        "endpointMs": 1063,
        "endOfTurnDelayMs": 3,
        "transcriptionDelayMs": 0,
        "e2eLatencyMs": 730,
        "replyGapMs": 1800,
    }
    assert timing.span_attributes()["dafter.turn.endpoint_ms"] == 1063
    assert timing.span_attributes()["dafter.turn.reply_gap_ms"] == 1800
    assert timing.log_fields()["endpoint"] == 1.063


def test_the_endpoint_and_gap_are_spent_by_one_reply() -> None:
    turns = Turns(Held(Endpoint(STOPPED, STOPPED + 1.0)).take_endpoint)
    greeting = turns.add(message("assistant", REPLY))
    turns.add(message("user", USER))
    reply = turns.add(message("assistant", REPLY))
    follow_up = turns.add(message("assistant", REPLY))
    assert greeting is not None and reply is not None and follow_up is not None
    assert "endpointMs" not in greeting.payload() and "replyGapMs" not in greeting.payload()
    assert reply.payload()["endpointMs"] == 1000
    assert "endpointMs" not in follow_up.payload() and "replyGapMs" not in follow_up.payload()


def test_no_gap_without_a_start_of_speech_after_the_voiced_audio() -> None:
    turns = Turns(
        Held(Endpoint(STOPPED, STOPPED + 1.0), Endpoint(STOPPED, STOPPED + 1.0)).take_endpoint
    )
    turns.add(message("user", USER))
    silent = turns.add(message("assistant", {"llm_node_ttft": 0.2}))
    turns.add(message("user", USER))
    early = turns.add(message("assistant", {"started_speaking_at": STOPPED - 0.1}))
    assert silent is not None and early is not None
    assert silent.payload() == {
        "turn": 0,
        "interrupted": False,
        "endpointMs": 1000,
        "endOfTurnDelayMs": 3,
        "transcriptionDelayMs": 0,
        "llmNodeTtftMs": 200,
    }
    assert "replyGapMs" not in early.payload()


def test_an_endpoint_released_before_its_voice_is_not_a_measurement() -> None:
    turns = Turns(Held(Endpoint(STOPPED, STOPPED - 0.5)).take_endpoint)
    turns.add(message("user", USER))
    timing = turns.add(message("assistant", REPLY))
    assert timing is not None
    assert "endpointMs" not in timing.payload() and "replyGapMs" not in timing.payload()


class Session:
    def __init__(self, stt: object) -> None:
        self.stt = stt
        self.usage = AgentSessionUsage(model_usage=[])
        self.handlers: dict[str, Callable[[Any], None]] = {}

    def on(self, name: str, handler: Callable[[Any], None]) -> None:
        self.handlers[name] = handler


def test_the_worker_takes_the_endpoint_its_speech_to_text_held() -> None:
    p = plan(load(JOB.read_bytes().strip()), "dafter-py")
    sent: list[bytes] = []

    async def publish(body: bytes) -> None:
        sent.append(body)

    session = Session(Held(Endpoint(STOPPED, STOPPED + 1.2)))
    registry = CollectorRegistry()

    async def run() -> None:
        events = SessionEvents(p.config, publish, clock=lambda: datetime(2026, 9, 28, tzinfo=UTC))
        watch(
            cast(AgentSession[Any], session), p, events, trace.NoOpTracer(), WorkerMetrics(registry)
        )
        added = session.handlers["conversation_item_added"]
        added(ConversationItemAddedEvent(item=message("user", USER)))
        added(ConversationItemAddedEvent(item=message("assistant", REPLY)))
        await events.drain()

    asyncio.run(run())
    timing = parse_event(sent[0])
    assert timing.type is EventType.AGENT_TURN_METRICS
    assert timing.payload["endpointMs"] == 1200
    assert timing.payload["replyGapMs"] == 1800
    labels = {
        "layer": "endpoint",
        "language": "hi",
        "channel": "webrtc",
        "stt": "sarvam/saaras:v3-realtime",
        "llm": "sarvam/sarvam-105b",
        "tts": "sarvam/bulbul:v3",
    }
    assert registry.get_sample_value(
        "dafter_agent_turn_layer_seconds_sum", labels
    ) == pytest.approx(1.2)
