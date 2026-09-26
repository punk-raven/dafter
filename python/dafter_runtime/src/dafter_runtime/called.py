from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine
from typing import Any

from livekit import rtc
from livekit.agents import AgentSession, JobContext
from livekit.agents.metrics import AgentSessionUsage
from livekit.agents.voice.events import AgentStateChangedEvent

from .addressing import Gate, Timer
from .answering import Roster, Voice
from .listeners import Listeners, is_human, listener_session
from .naming import Matcher
from .plan import Plan
from .stages import Stages, hearing

log = logging.getLogger("dafter.runtime.called")

CONTROL_TOPIC = "dafter.agent"
COMMANDS = frozenset({"wake"})


def gate_for(p: Plan, voice: Voice, loop: asyncio.AbstractEventLoop) -> Gate:
    addressing = p.config.agent.addressing

    def schedule(delay: float, callback: Callable[[], None]) -> Timer:
        return loop.call_later(delay, callback)

    return Gate(
        Matcher.for_addressing(addressing),
        addressing.follow_up_window_ms / 1000,
        voice,
        clock=loop.time,
        schedule=schedule,
        name=addressing.name,
    )


def listening(p: Plan, stages: Stages) -> Callable[[], AgentSession[Any]]:
    handling = hearing(p, stages)

    def new_session() -> AgentSession[Any]:
        return listener_session(stages.listener_stt(), stages.vad, handling)

    return new_session


def command(data: bytes) -> str | None:
    try:
        message = json.loads(data)
    except ValueError:
        return None
    if not isinstance(message, dict) or set(message) != {"action"}:
        return None
    action = message["action"]
    return action if action in COMMANDS else None


class Called:
    def __init__(
        self, ctx: JobContext, p: Plan, stages: Stages, session: AgentSession[Any], sample_rate: int
    ) -> None:
        self._ctx = ctx
        self._session = session
        self.roster = Roster()
        self.voice = Voice(session, self.roster, interruptible=p.config.turn.interruption.enabled)
        self.gate = gate_for(p, self.voice, asyncio.get_running_loop())
        self.listeners = Listeners(ctx.room, listening(p, stages), sample_rate, self.gate.heard)
        self._tasks: set[asyncio.Task[None]] = set()

    def addressee(self) -> str | None:
        return self.gate.addressee

    def usage(self) -> AgentSessionUsage:
        return AgentSessionUsage(
            model_usage=[*self._session.usage.model_usage, *self.listeners.usage()]
        )

    def _spawn(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _joined(self, participant: rtc.RemoteParticipant) -> None:
        if not is_human(participant, self._ctx.room):
            return
        self.roster.join(participant.identity, participant.name)
        self._spawn(self.listeners.join(participant.identity))

    def _left(self, participant: rtc.RemoteParticipant) -> None:
        if not is_human(participant, self._ctx.room):
            return
        self.roster.leave(participant.identity)
        self.gate.left(participant.identity)
        self._spawn(self.listeners.leave(participant.identity))
        if not self.roster.present():
            log.info("everyone left, closing the agent session")
            self._session.shutdown()

    def _state_changed(self, ev: AgentStateChangedEvent) -> None:
        self.gate.agent_state(ev.new_state)

    def _data(self, packet: rtc.DataPacket) -> None:
        if packet.topic != CONTROL_TOPIC:
            return
        sender = packet.participant
        if sender is None or not is_human(sender, self._ctx.room):
            return
        if command(packet.data) == "wake":
            log.info("woken from the wake control", extra={"by": sender.identity})
            self.gate.wake(sender.identity)

    def listen(self) -> None:
        self._session.on("agent_state_changed", self._state_changed)
        self._ctx.room.on("participant_connected", self._joined)
        self._ctx.room.on("participant_disconnected", self._left)
        self._ctx.room.on("data_received", self._data)
        for participant in self._ctx.room.remote_participants.values():
            self._joined(participant)
        self._ctx.add_shutdown_callback(self.listeners.aclose)


__all__ = ["CONTROL_TOPIC", "Called", "command", "gate_for", "listening"]
