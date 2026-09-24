from __future__ import annotations

import asyncio
import json
import logging
import secrets
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from dafter_core.config import ResolvedSessionConfig
from dafter_core.enums import AgentState, EventType
from dafter_core.errors import DafterError
from dafter_core.events import EventEnvelope

TOPIC = "dafter.events"
STATE_EVENT_VERSION = 1

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


class StateEvents:
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

    def envelope(self, state: AgentState, trace_id: str | None = None) -> EventEnvelope | None:
        if state is self._current:
            return None
        payload: dict[str, str] = {"state": str(state)}
        if self._current is not None:
            payload["previousState"] = str(self._current)
        event = EventEnvelope(
            event_id=new_event_id(),
            type=EventType.AGENT_STATE_CHANGED,
            version=STATE_EVENT_VERSION,
            session_id=self._cfg.session_id,
            tenant_id=self._cfg.tenant_id,
            sequence=self._sequence,
            occurred_at=self._clock(),
            trace_id=trace_id,
            payload=payload,
        )
        event.validate()
        self._sequence += 1
        self._current = state
        return event

    def changed(self, framework_state: str, trace_id: str | None = None) -> None:
        state = agent_state(framework_state)
        if state is None:
            return
        try:
            event = self.envelope(state, trace_id)
        except DafterError as exc:
            log.error("agent state event failed validation", extra={"code": str(exc.code)})
            return
        if event is None:
            return
        self._last = asyncio.ensure_future(self._send(event, self._last))

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
                "agent state event not delivered",
                extra={"sequence": event.sequence, "error": type(exc).__name__},
            )
