from __future__ import annotations

from typing import Any

import pytest
from dafter_runtime.timing import SERIAL_WAIT_MS, SPAN_NAME, Turns, TurnTiming
from livekit.agents.llm import ChatMessage
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter


def message(
    role: str, metrics: dict[str, Any] | None = None, interrupted: bool = False
) -> ChatMessage:
    return ChatMessage.model_validate(
        {"role": role, "content": ["x"], "interrupted": interrupted, "metrics": metrics or {}}
    )


USER = {
    "end_of_turn_delay": 0.6124,
    "transcription_delay": 0.488,
    "on_user_turn_completed_delay": 0.001,
    "stopped_speaking_at": 1_758_000_000.0,
}
AGENT = {
    "llm_node_ttft": 0.7341,
    "llm_node_ttfs": 1.0209,
    "tts_node_ttfb": 0.296,
    "playback_latency": 0.0031,
    "e2e_latency": 1.9678,
    "started_speaking_at": 1_758_000_002.0,
}


def test_a_reply_joins_the_user_layers_of_the_message_it_answers() -> None:
    turns = Turns()
    assert turns.add(message("user", USER)) is None
    timing = turns.add(message("assistant", AGENT))
    assert timing is not None
    assert timing.payload() == {
        "turn": 0,
        "interrupted": False,
        "endOfTurnDelayMs": 612,
        "transcriptionDelayMs": 488,
        "llmNodeTtftMs": 734,
        "llmNodeTtfsMs": 1021,
        "ttsNodeTtfbMs": 296,
        "playbackLatencyMs": 3,
        "e2eLatencyMs": 1968,
        "serial": False,
    }


def test_a_greeting_carries_no_user_layers_and_turns_count_from_zero() -> None:
    turns = Turns()
    greeting = turns.add(message("assistant", {"tts_node_ttfb": 0.2}))
    turns.add(message("user", USER))
    reply = turns.add(message("assistant", AGENT, interrupted=True))
    assert greeting is not None and reply is not None
    assert greeting.payload() == {"turn": 0, "interrupted": False, "ttsNodeTtfbMs": 200}
    assert reply.turn == 1 and reply.interrupted
    assert reply.payload()["endOfTurnDelayMs"] == 612


def test_user_layers_are_spent_by_one_reply() -> None:
    turns = Turns()
    turns.add(message("user", USER))
    turns.add(message("assistant", AGENT))
    follow_up = turns.add(message("assistant", {"llm_node_ttft": 0.5}))
    assert follow_up is not None
    assert follow_up.payload() == {"turn": 1, "interrupted": False, "llmNodeTtftMs": 500}


def test_a_later_user_message_replaces_one_that_got_no_reply() -> None:
    turns = Turns()
    turns.add(message("user", {"end_of_turn_delay": 5.0}))
    turns.add(message("user", {"transcription_delay": 0.25}))
    timing = turns.add(message("assistant"))
    assert timing is not None
    assert timing.payload() == {"turn": 0, "interrupted": False, "transcriptionDelayMs": 250}


def test_a_value_that_is_not_a_measurement_is_left_out() -> None:
    turns = Turns()
    timing = turns.add(message("assistant", {"e2e_latency": -0.01, "llm_node_ttft": 0.1}))
    assert timing is not None
    assert timing.payload() == {"turn": 0, "interrupted": False, "llmNodeTtftMs": 100}


def test_other_roles_are_not_turns() -> None:
    turns = Turns()
    assert turns.add(message("system")) is None
    assert turns.add(message("developer")) is None
    timing = turns.add(message("assistant"))
    assert timing is not None and timing.turn == 0


def test_log_fields_keep_seconds_and_the_span_carries_milliseconds() -> None:
    turns = Turns()
    turns.add(message("user", USER))
    timing = turns.add(message("assistant", AGENT))
    assert timing is not None
    assert timing.log_fields()["e2e_latency"] == 1.9678
    assert timing.log_fields()["end_of_turn_delay"] == 0.6124

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    timing.record(provider.get_tracer("test"))
    [span] = exporter.get_finished_spans()
    assert span.name == SPAN_NAME
    assert span.attributes is not None
    assert dict(span.attributes) == {
        "dafter.turn.index": 0,
        "dafter.turn.interrupted": False,
        "dafter.turn.end_of_turn_delay_ms": 612,
        "dafter.turn.transcription_delay_ms": 488,
        "dafter.turn.llm_node_ttft_ms": 734,
        "dafter.turn.llm_node_ttfs_ms": 1021,
        "dafter.turn.tts_node_ttfb_ms": 296,
        "dafter.turn.playback_latency_ms": 3,
        "dafter.turn.e2e_latency_ms": 1968,
        "dafter.turn.serial": False,
    }


def llm_layers(ttft: float, ttfs: float) -> TurnTiming:
    return TurnTiming(
        turn=0, interrupted=False, seconds={"llm_node_ttft": ttft, "llm_node_ttfs": ttfs}
    )


@pytest.mark.parametrize(
    ("ttft", "ttfs", "serial"),
    [
        (0.367, 0.52, False),
        (0.4, 0.9, False),
        (0.4, 0.901, True),
        (0.3, 2.4, True),
        (1.8, 1.9, False),
    ],
)
def test_a_turn_is_serial_when_its_first_sentence_waits_past_the_first_token(
    ttft: float, ttfs: float, serial: bool
) -> None:
    timing = llm_layers(ttft, ttfs)
    assert timing.serial() is serial
    assert timing.payload()["serial"] is serial
    assert timing.span_attributes()["dafter.turn.serial"] is serial
    assert timing.log_fields()["serial"] is serial


def test_the_rule_reads_the_milliseconds_the_row_carries() -> None:
    timing = llm_layers(0.4006, 0.9014)
    row = timing.payload()
    assert row["llmNodeTtfsMs"] - row["llmNodeTtftMs"] == SERIAL_WAIT_MS
    assert row["serial"] is False


@pytest.mark.parametrize(
    "seconds",
    [
        {"llm_node_ttft": 0.3, "tts_node_ttfb": 0.2},
        {"llm_node_ttfs": 2.0},
        {},
    ],
    ids=["interrupted before the first sentence", "no first token", "nothing measured"],
)
def test_a_turn_without_both_llm_layers_is_not_judged(seconds: dict[str, float]) -> None:
    timing = TurnTiming(turn=0, interrupted=True, seconds=seconds)
    assert timing.serial() is None
    assert "serial" not in timing.payload()
    assert "dafter.turn.serial" not in timing.span_attributes()
    assert "serial" not in timing.log_fields()
