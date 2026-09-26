from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from dafter_core.config import ResolvedSessionConfig
from dafter_core.enums import AgentState, EventType
from dafter_core.errors import DafterError
from dafter_core.events import EventEnvelope

TOPIC = "dafter.events"
EVENT_VERSION = 1

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

    def envelope(
        self, event_type: EventType, payload: dict[str, Any], trace_id: str | None = None
    ) -> EventEnvelope:
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

    def changed(self, framework_state: str, trace_id: str | None = None) -> None:
        state = agent_state(framework_state)
        if state is None or state is self._current:
            return
        payload = {"state": str(state)}
        if self._current is not None:
            payload["previousState"] = str(self._current)
        if self.emit(EventType.AGENT_STATE_CHANGED, payload, trace_id):
            self._current = state

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
