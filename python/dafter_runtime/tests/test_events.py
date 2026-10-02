from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from dafter_core.enums import AgentState, EventType
from dafter_core.events import parse_event
from dafter_runtime.events import StateEvents, agent_state
from dafter_runtime.plan import load

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


def emitter(sent: list[bytes]) -> StateEvents:
    async def publish(body: bytes) -> None:
        await asyncio.sleep(0)
        sent.append(body)

    cfg = load(JOB.read_bytes().strip())
    return StateEvents(cfg, publish, clock=lambda: datetime(2026, 9, 24, 10, 0, tzinfo=UTC))


def test_framework_states_outside_the_schema_are_not_events() -> None:
    assert agent_state("initializing") is None
    assert agent_state("speaking") is AgentState.SPEAKING


def test_each_change_is_one_valid_envelope_in_sequence() -> None:
    sent: list[bytes] = []

    async def run() -> None:
        events = emitter(sent)
        for state in ("initializing", "listening", "listening", "thinking", "speaking"):
            events.changed(state, TRACE)
        await events.drain()

    asyncio.run(run())
    parsed = [parse_event(body) for body in sent]
    assert [e.type for e in parsed] == [EventType.AGENT_STATE_CHANGED] * 3
    assert [e.sequence for e in parsed] == [0, 1, 2]
    assert [e.payload for e in parsed] == [
        {"state": "listening"},
        {"state": "thinking", "previousState": "listening"},
        {"state": "speaking", "previousState": "thinking"},
    ]
    assert all(e.session_id == "s_7f3a9c21" and e.trace_id == TRACE for e in parsed)
    assert json.loads(sent[0])["occurredAt"] == "2026-09-24T10:00:00Z"
