from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Coroutine
from functools import partial
from typing import Any

from dafter_core.speech import every_phrase
from livekit import rtc
from livekit.agents import AgentSession, JobContext
from livekit.agents.llm import ChatMessage, MetricsReport
from livekit.agents.metrics import AgentSessionUsage
from livekit.agents.voice.events import AgentStateChangedEvent, ConversationItemAddedEvent

from .addressing import BUSY_STATES, Gate, Timer, never_echoed, nobody_counted
from .answering import Roster, Voice
from .backchannel import Acknowledgements, Events, Filter, SessionFloor, acknowledged, is_question
from .barge_in import BargeIn, Resume, follow
from .captions import Captions
from .delivery import Delivery
from .listeners import Listeners, is_human, listener_session
from .naming import Matcher
from .noise import Meaning
from .plan import Plan
from .plausible import Plausible
from .presence import ALONE_GRACE_S, Alone
from .stages import Stages, hearing
from .switching import Switching

log = logging.getLogger("dafter.runtime.called")

CONTROL_TOPIC = "dafter.agent"
COMMANDS = frozenset({"wake"})


def gate_for(
    p: Plan,
    voice: Voice,
    loop: asyncio.AbstractEventLoop,
    present: Callable[[], int] = nobody_counted,
    echoed: Callable[[str], bool] = never_echoed,
) -> Gate:
    agent = p.config.agent

    def schedule(delay: float, callback: Callable[[], None]) -> Timer:
        return loop.call_later(delay, callback)

    return Gate(
        Matcher.for_agent(agent),
        agent.addressing.follow_up_window_ms / 1000,
        voice,
        clock=loop.time,
        schedule=schedule,
        name=agent.name or "",
        meaning=meaning_for(p),
        busy_words=p.config.turn.interruption.min_words,
        stays_awake=agent.addressing.stays_awake,
        present=present,
        echoed=echoed,
    )


def meaning_for(p: Plan) -> Meaning:
    return Meaning(every_phrase(p.config.turn.interruption.backchannel.words))


def resume_for(p: Plan, voice: Voice) -> Resume | None:
    interruption = p.config.turn.interruption
    if not interruption.resume_false_interruption or not interruption.false_interruption_timeout_ms:
        return None
    return Resume(voice.pause, voice.resume, interruption.false_interruption_timeout_ms / 1000)


def barge_in_for(p: Plan, gate: Gate, voice: Voice, loop: asyncio.AbstractEventLoop) -> BargeIn:
    interruption = p.config.turn.interruption
    return BargeIn(
        interruption.min_duration_ms / 1000,
        interruption.min_words,
        caller=lambda: gate.addressee,
        stop=voice.barge_in,
        clock=time.time,
        schedule=loop.call_later,
        resume=resume_for(p, voice),
        waits_for_words=Acknowledgements.of(interruption.backchannel) is not None,
        meaning=meaning_for(p),
    )


def listening(p: Plan, stages: Stages) -> Callable[[], AgentSession[Any]]:
    handling = hearing(p, stages)

    def new_session() -> AgentSession[Any]:
        return listener_session(stages.listener_stt(), stages.vad, handling)

    return new_session


def follow_replies(session: AgentSession[Any], gate: Gate) -> None:
    def added(ev: ConversationItemAddedEvent) -> None:
        item = ev.item
        if isinstance(item, ChatMessage) and item.role == "assistant":
            gate.replied(not item.interrupted and is_question(item.text_content or ""))

    session.on("conversation_item_added", added)


def through(sieves: list[Filter]) -> Filter:
    def apply(events: Events) -> Events:
        for sieve in sieves:
            events = sieve(events)
        return events

    return apply


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
        self,
        ctx: JobContext,
        p: Plan,
        stages: Stages,
        session: AgentSession[Any],
        sample_rate: int,
        switching: Switching | None = None,
        delivery: Delivery | None = None,
        captions: Captions | None = None,
    ) -> None:
        self._ctx = ctx
        self._delivery = delivery
        self._switching = switching if switching is not None and switching.enabled else None
        self._session = session
        self._captions = captions
        self.roster = Roster()
        self.voice = Voice(session, self.roster, interruptible=p.config.turn.interruption.enabled)
        loop = asyncio.get_running_loop()
        self.gate = gate_for(
            p,
            self.voice,
            loop,
            present=lambda: len(self.roster.present()),
            echoed=lambda speaker: self.barge_in.echoed(speaker),
        )
        self.barge_in = barge_in_for(p, self.gate, self.voice, loop)
        self._acknowledgements = Acknowledgements.of(p.config.turn.interruption.backchannel)
        self._floor = SessionFloor(session)
        self.listeners = Listeners(
            ctx.room,
            listening(p, stages),
            sample_rate,
            self._heard,
            self._listening,
            self._hearing,
        )
        self._tasks: set[asyncio.Task[None]] = set()
        self._alone = Alone(self._leave_alone, loop.call_later, ALONE_GRACE_S)

    def addressee(self) -> str | None:
        return self.gate.addressee

    def usage(self) -> AgentSessionUsage:
        return AgentSessionUsage(
            model_usage=[*self._session.usage.model_usage, *self.listeners.usage()]
        )

    def _heard(self, speaker: str, text: str, timing: MetricsReport) -> None:
        self.barge_in.committed(speaker)
        self.gate.heard(speaker, text, timing)

    def _listening(self, speaker: str, session: AgentSession[Any]) -> None:
        follow(self.barge_in, speaker, session)
        if self._delivery is not None:
            self._delivery.filler.hears(session)
        if self._captions is not None:
            self._captions.follow(speaker, session)

    def _hearing(self, speaker: str, session: AgentSession[Any]) -> Filter:
        sieves: list[Filter] = [Plausible()]
        if self._delivery is not None:
            echoed = partial(self.barge_in.echoing, speaker)
            sieves.append(self._delivery.own_voice.hearing(session, echoed))
        if self._switching is not None:
            sieves.append(self._switching.observe(speaker))
        sieves.append(
            acknowledged(
                self._acknowledgements,
                self._floor,
                lambda: self.barge_in.acknowledged(speaker),
            )
        )
        return through(sieves)

    def _spawn(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _joined(self, participant: rtc.RemoteParticipant) -> None:
        if not is_human(participant, self._ctx.room):
            return
        self._alone.joined()
        self.roster.join(participant.identity, participant.name)
        self._spawn(self.listeners.join(participant.identity))

    def _left(self, participant: rtc.RemoteParticipant) -> None:
        if not is_human(participant, self._ctx.room):
            return
        self.roster.leave(participant.identity)
        self.gate.left(participant.identity)
        self.barge_in.left(participant.identity)
        if self._captions is not None:
            self._captions.left(participant.identity)
        self._spawn(self.listeners.leave(participant.identity))
        if not self.roster.present():
            log.info("everyone left", extra={"leaving_after_s": ALONE_GRACE_S})
            self._alone.emptied()

    def _leave_alone(self) -> None:
        log.info("nobody came back, the agent leaves the call")
        self._ctx.shutdown(reason="everyone left")

    def _state_changed(self, ev: AgentStateChangedEvent) -> None:
        self.gate.agent_state(ev.new_state)
        if ev.new_state in BUSY_STATES:
            self.barge_in.replying()

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
        follow_replies(self._session, self.gate)
        self._ctx.room.on("participant_connected", self._joined)
        self._ctx.room.on("participant_disconnected", self._left)
        self._ctx.room.on("data_received", self._data)
        for participant in self._ctx.room.remote_participants.values():
            self._joined(participant)
        self._ctx.add_shutdown_callback(self.listeners.aclose)


__all__ = [
    "CONTROL_TOPIC",
    "Called",
    "barge_in_for",
    "command",
    "follow_replies",
    "gate_for",
    "listening",
    "resume_for",
    "through",
]
