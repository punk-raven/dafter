from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dafter_core.enums import AgentState, EventType
from dafter_core.events import parse_event
from dafter_core.hashing import seal
from dafter_runtime.events import SessionEvents, agent_state
from dafter_runtime.plan import load

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
TRACE = "4bf92f3577b34da6a3ce929d0e0e4736"


def emitter(sent: list[bytes], version: dict[str, Any] | None = None) -> SessionEvents:
    async def publish(body: bytes) -> None:
        await asyncio.sleep(0)
        sent.append(body)

    doc = json.loads(JOB.read_bytes())
    doc["agent"]["addressing"]["mode"] = "always"
    doc.pop("version", None)
    if version is not None:
        doc["version"] = version
    sealed, _ = seal(json.dumps(doc))
    cfg = load(sealed)
    return SessionEvents(cfg, publish, clock=lambda: datetime(2026, 9, 24, 10, 0, tzinfo=UTC))


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


def test_every_event_type_shares_one_sequence() -> None:
    sent: list[bytes] = []

    async def run() -> None:
        events = emitter(sent)
        events.changed("listening")
        events.emit(EventType.AGENT_TURN_METRICS, {"turn": 0, "interrupted": False}, TRACE)
        events.changed("speaking")
        await events.drain()

    asyncio.run(run())
    parsed = [parse_event(body) for body in sent]
    assert [e.type for e in parsed] == [
        EventType.AGENT_STATE_CHANGED,
        EventType.AGENT_TURN_METRICS,
        EventType.AGENT_STATE_CHANGED,
    ]
    assert [e.sequence for e in parsed] == [0, 1, 2]


def test_an_invalid_payload_is_dropped_without_spending_a_sequence_number() -> None:
    sent: list[bytes] = []

    async def run() -> None:
        events = emitter(sent)
        assert not events.emit(EventType.AGENT_TURN_METRICS, {"turn": -1, "interrupted": False})
        assert events.emit(EventType.AGENT_TURN_METRICS, {"turn": 0, "interrupted": True})
        await events.drain()

    asyncio.run(run())
    assert [parse_event(body).sequence for body in sent] == [0]


SCORED = {
    "segmentId": "sg_0000000000000001",
    "source": {"provider": "sarvam", "model": "sarvam-105b"},
    "score": 1.0,
    "criteria": {"correctness": "pass"},
}
CONFIGURED = {
    "llm": {"provider": "sarvam", "model": "sarvam-105b"},
    "fillers": True,
    "backchannel": True,
    "normalization": "platform",
}


def versioned_payloads(version: dict[str, Any] | None) -> list[dict[str, Any]]:
    sent: list[bytes] = []

    async def run() -> None:
        events = emitter(sent, version)
        events.emit(EventType.AGENT_CONFIGURED, CONFIGURED)
        events.changed("listening")
        events.emit(EventType.AGENT_TURN_METRICS, {"turn": 0, "interrupted": False}, TRACE)
        events.emit(EventType.AGENT_TURN_SCORED, SCORED)
        await events.drain()

    asyncio.run(run())
    return [parse_event(body).payload for body in sent]


def test_configured_turn_metrics_and_scores_carry_the_version_the_session_runs() -> None:
    configured, state, turn, scored = versioned_payloads({"id": "support-v4", "candidate": True})
    assert configured["configVersion"] == {"id": "support-v4", "arm": "candidate"}
    assert turn["configVersion"] == {"id": "support-v4", "arm": "candidate"}
    assert scored["configVersion"] == {"id": "support-v4", "arm": "candidate"}
    assert "configVersion" not in state


def test_a_stable_session_says_so() -> None:
    _, _, turn, scored = versioned_payloads({"id": "support-v3"})
    assert turn["configVersion"] == {"id": "support-v3", "arm": "stable"}
    assert scored["configVersion"] == {"id": "support-v3", "arm": "stable"}


def test_an_unversioned_session_sends_no_version() -> None:
    assert all("configVersion" not in p for p in versioned_payloads(None))
