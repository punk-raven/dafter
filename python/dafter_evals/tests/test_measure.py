from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from dafter_core.config import Budgets
from dafter_core.enums import EventType
from dafter_core.events import EventEnvelope
from dafter_evals.measure import compare, layers, percentile, state_stop
from dafter_evals.probe import Events
from dafter_evals.turns import OverlapResult, summarize

BUDGETS = Budgets(turn_gap_p50_ms=800, turn_gap_p95_ms=1500, barge_in_stop_p50_ms=300)


def envelope(sequence: int, event_type: EventType, payload: dict[str, Any]) -> bytes:
    event = EventEnvelope(
        event_id=f"e_{sequence:032x}",
        type=event_type,
        version=1,
        session_id="s_7f3a9c21",
        tenant_id="t_9c21a4be",
        sequence=sequence,
        occurred_at=datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
        trace_id=None,
        payload=payload,
    )
    return json.dumps(event.to_dict()).encode()


def metrics(turn: int, e2e: int, **layers_ms: int) -> dict[str, Any]:
    return {"turn": turn, "interrupted": False, "e2eLatencyMs": e2e, **layers_ms}


def test_percentiles_interpolate_between_ranks() -> None:
    assert percentile([], 0.5) is None
    assert percentile([100], 0.95) == 100
    assert percentile([100, 200, 300, 400], 0.5) == 250
    assert percentile(list(range(1, 21)), 0.95) == 19


def test_the_probe_keeps_states_and_turn_metrics_apart() -> None:
    events = Events()
    events.receive(envelope(0, EventType.AGENT_STATE_CHANGED, {"state": "speaking"}), 1.0)
    events.receive(envelope(1, EventType.AGENT_TURN_METRICS, metrics(0, 700)), 1.1)
    events.receive(
        envelope(
            2, EventType.AGENT_STATE_CHANGED, {"state": "listening", "previousState": "speaking"}
        ),
        2.0,
    )
    events.receive(b"{not an event", 2.1)
    assert events.states == [(1.0, "speaking"), (2.0, "listening")]
    assert events.turn_metrics == [metrics(0, 700)]
    assert events.errors == 1


def test_each_layer_reports_its_median_and_tail_over_the_turns_that_carry_it() -> None:
    turns = [metrics(i, 600 + 100 * i, llmNodeTtftMs=300 + 10 * i) for i in range(10)]
    turns.append({"turn": 10, "interrupted": True})
    by_layer = layers(turns)
    assert by_layer["e2eLatencyMs"] == {"n": 10, "p50": 1050, "p95": 1455}
    assert by_layer["llmNodeTtftMs"] == {"n": 10, "p50": 345, "p95": 386}
    assert by_layer["ttsNodeTtfbMs"] == {"n": 0, "p50": None, "p95": None}


def test_the_barge_in_stop_is_the_first_state_that_is_not_speaking() -> None:
    states = [(1.0, "speaking"), (1.9, "speaking"), (2.25, "listening"), (2.6, "thinking")]
    assert state_stop(states, onset=2.0, until=3.0) == 250
    assert state_stop(states, onset=2.0, until=2.2) is None


def barge_in(stop: int | None, inconclusive: bool = False) -> OverlapResult:
    return OverlapResult("barge_in", "रुकिए", 1000, True, 300, [], False, inconclusive, stop)


def report(e2e: list[int], stops: list[OverlapResult]) -> dict[str, Any]:
    events = Events(turn_metrics=[metrics(i, v) for i, v in enumerate(e2e)])
    summary: dict[str, Any] = summarize([], stops, events, BUDGETS)["summary"]
    return summary


def test_go_needs_the_reply_median_and_tail_and_every_barge_in_under_budget() -> None:
    fast = report([500, 600, 700, 1400], [barge_in(200), barge_in(280), barge_in(None, True)])
    assert fast["verdict"]["go"] is True

    slow_tail = report([500, 600, 700, 2400], [barge_in(200)])
    assert slow_tail["verdict"]["go"] is False
    assert slow_tail["verdict"]["checks"]["replyP95"]["pass"] is False

    missed = report([500, 600], [barge_in(200), barge_in(None)])
    assert missed["verdict"]["checks"]["bargeInStopP50"] == {
        "measuredMs": 200,
        "budgetMs": 300,
        "pass": False,
        "unstopped": 1,
    }

    nothing = report([], [])
    assert nothing["verdict"]["go"] is False


def test_a_run_compares_against_an_earlier_report() -> None:
    before = report([2000, 2100, 2250], [barge_in(1250)])
    after = report([700, 750, 900], [barge_in(260)])
    diff = compare(before, after)
    assert diff["deltaMs"]["e2eLatencyMs"] == {"p50": -1350, "p95": -1350}
    assert diff["deltaMs"]["bargeInStopP50Ms"] == -990
    assert diff["deltaMs"]["ttsNodeTtfbMs"] == {"p50": None, "p95": None}
    assert diff["go"] == {"before": False, "after": True}
