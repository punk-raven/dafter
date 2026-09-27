from __future__ import annotations

import logging
from collections.abc import AsyncIterable, Callable
from typing import Any

from dafter_core.enums import Role
from dafter_providers import Styled
from livekit import rtc
from livekit.agents import Agent, AgentSession, FlushSentinel, ModelSettings
from livekit.agents import llm as lk_llm

from .answering import Roster
from .backchannel import Acknowledgements, Events, acknowledged, holds_floor
from .consent import Confirmations
from .delivery import Delivery
from .everyday import current_time, go_quiet, who_is_here
from .listeners import is_human
from .naming import words
from .plan import Plan
from .tools import NO_FILLING, Filling, Registry, Tool

log = logging.getLogger("dafter.runtime.toolbox")


class Answering(Agent):
    def __init__(
        self,
        instructions: str,
        registry: Registry,
        caller: Callable[[], str | None],
        acknowledgements: Acknowledgements | None = None,
        delivery: Delivery | None = None,
    ):
        super().__init__(instructions=instructions, tools=registry.function_tools())
        self._registry = registry
        self._caller = caller
        self._acknowledgements = acknowledgements
        self._delivery = delivery

    def stt_node(
        self, audio: AsyncIterable[rtc.AudioFrame], model_settings: ModelSettings
    ) -> Events:
        events = Agent.default.stt_node(self, audio, model_settings)
        return acknowledged(self._acknowledgements, lambda: holds_floor(self.session))(events)

    def llm_node(
        self, chat_ctx: lk_llm.ChatContext, tools: list[lk_llm.Tool], model_settings: ModelSettings
    ) -> AsyncIterable[lk_llm.ChatChunk | str | FlushSentinel]:
        if self._delivery is not None:
            self._delivery.heard(chat_ctx)
        return Agent.default.llm_node(self, chat_ctx, tools, model_settings)

    def tts_node(
        self, text: AsyncIterable[str], model_settings: ModelSettings
    ) -> AsyncIterable[rtc.AudioFrame]:
        if self._delivery is not None and isinstance(voice := self.session.tts, Styled):
            voice.style(str(self._delivery.situation))
        reply = Agent.default.tts_node(self, text, model_settings)
        if self._delivery is None or not self._delivery.filler.enabled:
            return reply
        return self._delivery.filler.ahead(reply, lambda: self.session.current_speech)

    async def on_user_turn_completed(
        self, turn_ctx: lk_llm.ChatContext, new_message: lk_llm.ChatMessage
    ) -> None:
        caller = self._caller()
        if caller is not None:
            self._registry.heard(caller, new_message.text_content or "")


def unattested(identity: str) -> Role | None:
    return None


def name_words(p: Plan) -> frozenset[str]:
    a = p.config.agent.addressing
    return frozenset(w for n in (a.name, *a.aliases) for w in words(n))


def linked(session: AgentSession[Any]) -> Callable[[], str | None]:
    def caller() -> str | None:
        try:
            participant = session.room_io.linked_participant
        except RuntimeError:
            return None
        return participant.identity if participant is not None else None

    return caller


def delivered(session: AgentSession[Any]) -> Callable[[str, str], None]:
    def deliver(tool: str, result: str) -> None:
        log.info("tool finished in the background", extra={"tool": tool})
        session.generate_reply(
            instructions=f"The {tool} tool has finished: {result} Tell the person briefly."
        )

    return deliver


def follow(room: rtc.Room, roster: Roster) -> None:
    def joined(participant: rtc.RemoteParticipant) -> None:
        if is_human(participant, room):
            roster.join(participant.identity, participant.name)

    def left(participant: rtc.RemoteParticipant) -> None:
        roster.leave(participant.identity)

    room.on("participant_connected", joined)
    room.on("participant_disconnected", left)
    for participant in room.remote_participants.values():
        joined(participant)


def everyday(roster: Roster, sleep: Callable[[], None] | None) -> list[Tool]:
    tools = [current_time(), who_is_here(roster.present)]
    if sleep is not None:
        tools.append(go_quiet(sleep))
    return tools


def registry_for(
    p: Plan,
    session: AgentSession[Any],
    roster: Roster,
    caller: Callable[[], str | None],
    sleep: Callable[[], None] | None,
    delivery: Delivery | None = None,
) -> Registry:
    filler = delivery.filler if delivery is not None else None
    return Registry(
        everyday(roster, sleep),
        caller=caller,
        role_of=unattested,
        confirmations=Confirmations(name_words(p)),
        deliver=delivered(session),
        filling=Filling(filler.phrase, filler.after) if filler is not None else NO_FILLING,
        shared_filler=filler is not None and filler.enabled,
    )


__all__ = ["Answering", "everyday", "follow", "linked", "name_words", "registry_for"]
