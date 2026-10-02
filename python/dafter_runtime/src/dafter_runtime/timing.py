from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from livekit.agents.llm import ChatMessage
from opentelemetry import trace

ENDPOINT = "endpoint"
REPLY_GAP = "reply_gap"
USER_LAYERS = ("end_of_turn_delay", "transcription_delay")
AGENT_LAYERS = (
    "llm_node_ttft",
    "llm_node_ttfs",
    "tts_node_ttfb",
    "playback_latency",
    "e2e_latency",
)
PAYLOAD_FIELDS = {
    "endpoint": "endpointMs",
    "end_of_turn_delay": "endOfTurnDelayMs",
    "transcription_delay": "transcriptionDelayMs",
    "llm_node_ttft": "llmNodeTtftMs",
    "llm_node_ttfs": "llmNodeTtfsMs",
    "tts_node_ttfb": "ttsNodeTtfbMs",
    "playback_latency": "playbackLatencyMs",
    "e2e_latency": "e2eLatencyMs",
    "reply_gap": "replyGapMs",
}
SPAN_NAME = "dafter.agent_turn"
SERIAL_WAIT_MS = 500
FIRST_SENTENCE_MOSTLY = 0.8
SENTENCE_END = re.compile(r"[.!?\u0964\u0965]+(?=\s|$)")


def first_sentence_share(text: str | None) -> float | None:
    reply = (text or "").strip()
    if not reply:
        return None
    end = SENTENCE_END.search(reply)
    return (end.end() if end else len(reply)) / len(reply)


def _layers(metrics: Mapping[str, Any], keys: tuple[str, ...]) -> dict[str, float]:
    found: dict[str, float] = {}
    for key in keys:
        value = metrics.get(key)
        if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
            found[key] = float(value)
    return found


@dataclass(frozen=True, slots=True)
class TurnTiming:
    turn: int
    interrupted: bool
    seconds: dict[str, float] = field(default_factory=dict)
    first_sentence_share: float | None = None

    def milliseconds(self) -> dict[str, int]:
        return {key: round(value * 1000) for key, value in self.seconds.items()}

    def serial(self) -> bool | None:
        ms = self.milliseconds()
        if "llm_node_ttft" not in ms or "llm_node_ttfs" not in ms:
            return None
        if ms["llm_node_ttfs"] - ms["llm_node_ttft"] <= SERIAL_WAIT_MS:
            return False
        share = self.first_sentence_share
        return share is None or share < FIRST_SENTENCE_MOSTLY

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
        if self.first_sentence_share is not None:
            fields["first_sentence_share"] = round(self.first_sentence_share, 2)
        return fields

    def record(self, tracer: trace.Tracer) -> None:
        tracer.start_span(SPAN_NAME, attributes=self.span_attributes()).end()


class Turns:
    def __init__(self, endpoint: Callable[[], tuple[float, float] | None] | None = None) -> None:
        self._count = 0
        self._endpoint = endpoint
        self._user: dict[str, float] = {}
        self._voiced_until: float | None = None

    def _user_turn(self, item: ChatMessage) -> None:
        self._user = {}
        self._voiced_until = None
        found = self._endpoint() if self._endpoint is not None else None
        if found is not None and found[1] >= found[0]:
            voiced_until, released_at = found
            self._user[ENDPOINT] = released_at - voiced_until
            self._voiced_until = voiced_until
        self._user.update(_layers(item.metrics, USER_LAYERS))

    def _reply_gap(self, item: ChatMessage) -> dict[str, float]:
        voiced_until, self._voiced_until = self._voiced_until, None
        started = item.metrics.get("started_speaking_at")
        if voiced_until is None or not isinstance(started, int | float) or started < voiced_until:
            return {}
        return {REPLY_GAP: float(started) - voiced_until}

    def add(self, item: ChatMessage) -> TurnTiming | None:
        if item.role == "user":
            self._user_turn(item)
            return None
        if item.role != "assistant":
            return None
        seconds = {**self._user, **_layers(item.metrics, AGENT_LAYERS), **self._reply_gap(item)}
        self._user = {}
        timing = TurnTiming(
            turn=self._count,
            interrupted=item.interrupted,
            seconds=seconds,
            first_sentence_share=first_sentence_share(item.text_content),
        )
        self._count += 1
        return timing

    def unheard(self) -> TurnTiming:
        timing = TurnTiming(turn=self._count, interrupted=True, seconds=self._user)
        self._user = {}
        self._voiced_until = None
        self._count += 1
        return timing
