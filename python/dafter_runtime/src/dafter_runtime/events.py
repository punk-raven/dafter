from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from dafter_core.config import ResolvedSessionConfig
from dafter_core.enums import AgentState, EventType, WakeSource
from dafter_core.errors import DafterError
from dafter_core.events import EventEnvelope

TOPIC = "dafter.events"
EVENT_VERSION = 1
VERSIONED = frozenset(
    {EventType.AGENT_CONFIGURED, EventType.AGENT_TURN_METRICS, EventType.AGENT_TURN_SCORED}
)

log = logging.getLogger("dafter.runtime.events")

Publish = Callable[[bytes], Awaitable[None]]
Clock = Callable[[], datetime]


def agent_state(framework_state: str) -> AgentState | None:
    try:
        return AgentState(framework_state)
    except ValueError:
        return None


def new_event_id() -> str:
    return "e_" + secrets.token_hex(16)


class SessionEvents:
    def __init__(
        self,
        cfg: ResolvedSessionConfig,
        publish: Publish,
        clock: Clock = lambda: datetime.now(UTC),
    ) -> None:
        self._cfg = cfg
        self._publish = publish
        self._clock = clock
        self._sequence = 0
        self._current: AgentState | None = None
        self._last: asyncio.Future[None] | None = None
        self._called = cfg.agent.addressing.waits_to_be_called
        self._awake: tuple[str, WakeSource] | None = None

    def versioned(self, event_type: EventType, payload: dict[str, Any]) -> dict[str, Any]:
        version = self._cfg.version
        if event_type not in VERSIONED or version is None:
            return payload
        return {**payload, "configVersion": {"id": version.id, "arm": version.arm}}

    def envelope(
        self, event_type: EventType, payload: dict[str, Any], trace_id: str | None = None
    ) -> EventEnvelope:
        payload = self.versioned(event_type, payload)
        event = EventEnvelope(
            event_id=new_event_id(),
            type=event_type,
            version=EVENT_VERSION,
            session_id=self._cfg.session_id,
            tenant_id=self._cfg.tenant_id,
            sequence=self._sequence,
            occurred_at=self._clock(),
            trace_id=trace_id,
            payload=payload,
        )
        event.validate()
        self._sequence += 1
        return event

    def emit(
        self, event_type: EventType, payload: dict[str, Any], trace_id: str | None = None
    ) -> bool:
        try:
            event = self.envelope(event_type, payload, trace_id)
        except DafterError as exc:
            log.error(
                "event failed validation",
                extra={"type": str(event_type), "code": str(exc.code)},
            )
            return False
        self._last = asyncio.ensure_future(self._send(event, self._last))
        return True

    def _state(self, state: AgentState, previous: AgentState | None) -> dict[str, Any]:
        payload: dict[str, Any] = {"state": str(state)}
        if previous is not None:
            payload["previousState"] = str(previous)
        if self._called:
            payload["dormant"] = self._awake is None
        if self._awake is not None:
            payload["wokenBy"], payload["wokenVia"] = self._awake[0], str(self._awake[1])
        return payload

    def changed(self, framework_state: str, trace_id: str | None = None) -> None:
        state = agent_state(framework_state)
        if state is None or state is self._current:
            return
        if self.emit(EventType.AGENT_STATE_CHANGED, self._state(state, self._current), trace_id):
            self._current = state

    def addressed(
        self, woken_by: str | None, via: WakeSource | None, trace_id: str | None = None
    ) -> None:
        awake = (woken_by, via) if woken_by is not None and via is not None else None
        if awake == self._awake:
            return
        self._awake = awake
        if self._current is not None:
            self.emit(EventType.AGENT_STATE_CHANGED, self._state(self._current, None), trace_id)

    async def drain(self) -> None:
        if self._last is not None:
            await self._last

    async def _send(self, event: EventEnvelope, previous: asyncio.Future[None] | None) -> None:
        if previous is not None:
            await previous
        body = json.dumps(event.to_dict(), separators=(",", ":")).encode()
        try:
            await self._publish(body)
        except Exception as exc:
            log.warning(
                "event not delivered",
                extra={
                    "type": str(event.type),
                    "sequence": event.sequence,
                    "error": type(exc).__name__,
                },
            )
