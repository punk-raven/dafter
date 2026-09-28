from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from dafter_core.enums import Role
from livekit import rtc
from livekit.agents import Agent, AgentSession
from livekit.agents import llm as lk_llm

from .answering import Roster
from .consent import Confirmations
from .everyday import current_time, go_quiet, who_is_here
from .listeners import is_human
from .naming import words
from .plan import Plan
from .tools import Registry, Tool

log = logging.getLogger("dafter.runtime.toolbox")


class Answering(Agent):
    def __init__(self, instructions: str, registry: Registry, caller: Callable[[], str | None]):
        super().__init__(instructions=instructions, tools=registry.function_tools())
        self._registry = registry
        self._caller = caller

    async def on_user_turn_completed(
        self, turn_ctx: lk_llm.ChatContext, new_message: lk_llm.ChatMessage
    ) -> None:
        caller = self._caller()
        if caller is not None:
            self._registry.heard(caller, new_message.text_content or "")


def unattested(identity: str) -> Role | None:
    return None


def name_words(p: Plan) -> frozenset[str]:
    agent = p.config.agent
    return frozenset(w for n in (agent.name or "", *agent.addressing.aliases) for w in words(n))


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
) -> Registry:
    return Registry(
        everyday(roster, sleep),
        caller=caller,
        role_of=unattested,
        confirmations=Confirmations(name_words(p)),
        deliver=delivered(session),
    )


__all__ = ["Answering", "everyday", "follow", "linked", "name_words", "registry_for"]
