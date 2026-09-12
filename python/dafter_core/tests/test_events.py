from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from dafter_core.enums import AgentState, ErrorCode, EventType
from dafter_core.errors import DafterError
from dafter_core.events import EventEnvelope, parse_event

AT = datetime(2026, 9, 11, 10, 0, 0, tzinfo=UTC)


def event(typ: EventType, payload: dict[str, Any] | None = None, **kw: Any) -> EventEnvelope:
    return EventEnvelope(
        event_id="e_0123456789abcdef0123456789abcdef",
        type=typ,
        version=1,
        session_id="s_7f3a9c21",
        tenant_id="t_9c21a4be",
        sequence=0,
        occurred_at=AT,
        payload=payload or {},
        **kw,
    )


def refuse(e: EventEnvelope) -> DafterError:
    with pytest.raises(DafterError) as exc:
        e.validate()
    return exc.value


def test_a_well_formed_event_validates() -> None:
    event(
        EventType.AGENT_STATE_CHANGED,
        {"state": AgentState.THINKING, "previousState": AgentState.LISTENING},
    ).validate()


@pytest.mark.parametrize(
    ("name", "payload"),
    [
        ("wrong enum case", {"state": "THINKING"}),
        ("invented state", {"state": "pondering"}),
        ("missing required state", {}),
        ("typo in field name", {"state": "thinking", "stat": "x"}),
    ],
)
def test_typed_payloads_are_enforced(name: str, payload: dict[str, Any]) -> None:
    err = refuse(event(EventType.AGENT_STATE_CHANGED, payload))
    assert err.code is ErrorCode.INTERNAL, name
    assert err.details, name


def test_untyped_events_still_accept_anything() -> None:
    event(EventType.RECORDING_SEALED, {"whatever": [1, 2]}).validate()


def test_a_non_opaque_session_id_is_refused() -> None:
    e = event(EventType.SESSION_SIGNAL, {"name": "handoff"})
    assert refuse(EventEnvelope(**{**vars_of(e), "session_id": "call-with-jane@example.com"}))


def vars_of(e: EventEnvelope) -> dict[str, Any]:
    return {f: getattr(e, f) for f in e.__slots__}


@pytest.mark.parametrize(
    "tz", [UTC, timezone(timedelta(hours=5, minutes=30)), timezone(timedelta(hours=-8))]
)
def test_offsets_the_platform_produces_are_accepted(tz: timezone) -> None:
    e = event(EventType.SESSION_SIGNAL, {"name": "handoff"})
    EventEnvelope(**{**vars_of(e), "occurred_at": AT.astimezone(tz)}).validate()


def test_a_naive_timestamp_is_refused_before_it_reaches_the_wire() -> None:
    e = event(EventType.SESSION_SIGNAL, {"name": "handoff"})
    naive = EventEnvelope(**{**vars_of(e), "occurred_at": datetime(2026, 9, 11, 10, 0, 0)})
    with pytest.raises(DafterError) as exc:
        naive.validate()
    assert "timezone" in exc.value.message


def test_utc_is_written_as_Z_matching_go() -> None:
    assert event(EventType.SESSION_SIGNAL, {"name": "x"}).to_dict()["occurredAt"].endswith("Z")


def test_round_trips_through_the_wire_form() -> None:
    original = event(EventType.AGENT_STATE_CHANGED, {"state": "thinking"}, trace_id="a" * 32)
    back = parse_event(json.dumps(original.to_dict()))
    assert back == original


def test_parse_event_refuses_a_malformed_document() -> None:
    with pytest.raises(DafterError) as exc:
        parse_event('{"eventId":"e_short","type":"session.created"}')
    assert exc.value.code is ErrorCode.INTERNAL


def test_parse_event_refuses_a_timestamp_that_is_not_a_calendar_date() -> None:
    raw = event(EventType.SESSION_SIGNAL, {"name": "handoff"}).to_dict()
    raw["occurredAt"] = "2026-13-45T25:61:61Z"
    with pytest.raises(DafterError) as exc:
        parse_event(json.dumps(raw))
    assert any(p.startswith("at '/occurredAt':") for p in exc.value.details)


def test_parse_event_refuses_malformed_json_as_internal() -> None:
    with pytest.raises(DafterError) as exc:
        parse_event('{"eventId":')
    assert exc.value.code is ErrorCode.INTERNAL
