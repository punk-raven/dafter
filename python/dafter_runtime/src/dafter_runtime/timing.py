from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from livekit.agents.llm import ChatMessage, MetricsReport
from opentelemetry import trace

USER_LAYERS = ("end_of_turn_delay", "transcription_delay")
HEARD_AT = ("started_speaking_at", "stopped_speaking_at")
AGENT_LAYERS = (
    "llm_node_ttft",
    "llm_node_ttfs",
    "tts_node_ttfb",
    "playback_latency",
    "e2e_latency",
)
PAYLOAD_FIELDS = {
    "end_of_turn_delay": "endOfTurnDelayMs",
    "transcription_delay": "transcriptionDelayMs",
    "llm_node_ttft": "llmNodeTtftMs",
    "llm_node_ttfs": "llmNodeTtfsMs",
    "tts_node_ttfb": "ttsNodeTtfbMs",
    "playback_latency": "playbackLatencyMs",
    "e2e_latency": "e2eLatencyMs",
}
SPAN_NAME = "dafter.agent_turn"
SERIAL_WAIT_MS = 500


def _layers(metrics: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, float]:
    found: dict[str, float] = {}
    for key in keys:
        value = metrics.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
            found[key] = float(value)
    return found


def heard(metrics: Mapping[str, Any] | None) -> MetricsReport:
    return cast(MetricsReport, _layers(metrics or {}, (*HEARD_AT, *USER_LAYERS)))


@dataclass(frozen=True, slots=True)
class TurnTiming:
    turn: int
    interrupted: bool
    seconds: dict[str, float] = field(default_factory=dict)

    def milliseconds(self) -> dict[str, int]:
        return {key: round(value * 1000) for key, value in self.seconds.items()}

    def serial(self) -> bool | None:
        ms = self.milliseconds()
        if "llm_node_ttft" not in ms or "llm_node_ttfs" not in ms:
            return None
        return ms["llm_node_ttfs"] - ms["llm_node_ttft"] > SERIAL_WAIT_MS

    def payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {"turn": self.turn, "interrupted": self.interrupted}
        body.update({PAYLOAD_FIELDS[k]: ms for k, ms in self.milliseconds().items()})
        if (serial := self.serial()) is not None:
            body["serial"] = serial
        return body

    def span_attributes(self) -> dict[str, int | bool]:
        attrs: dict[str, int | bool] = {
            "dafter.turn.index": self.turn,
            "dafter.turn.interrupted": self.interrupted,
        }
        attrs.update({f"dafter.turn.{k}_ms": ms for k, ms in self.milliseconds().items()})
        if (serial := self.serial()) is not None:
            attrs["dafter.turn.serial"] = serial
        return attrs

    def log_fields(self) -> dict[str, Any]:
        fields: dict[str, Any] = {"turn": self.turn, "interrupted": self.interrupted}
        fields.update({k: round(v, 4) for k, v in self.seconds.items()})
        if (serial := self.serial()) is not None:
            fields["serial"] = serial
        return fields

    def record(self, tracer: trace.Tracer) -> None:
        tracer.start_span(SPAN_NAME, attributes=self.span_attributes()).end()


class Turns:
    def __init__(self) -> None:
        self._count = 0
        self._user: dict[str, float] = {}

    def add(self, item: ChatMessage) -> TurnTiming | None:
        if item.role == "user":
            self._user = _layers(item.metrics, USER_LAYERS)
            return None
        if item.role != "assistant":
            return None
        seconds = {**self._user, **_layers(item.metrics, AGENT_LAYERS)}
        self._user = {}
        timing = TurnTiming(turn=self._count, interrupted=item.interrupted, seconds=seconds)
        self._count += 1
        return timing
