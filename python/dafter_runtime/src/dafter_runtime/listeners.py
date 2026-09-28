from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from livekit import rtc
from livekit.agents import Agent, AgentSession, StopResponse
from livekit.agents import llm as lk_llm
from livekit.agents import stt as lk_stt
from livekit.agents import vad as lk_vad
from livekit.agents.job import DEFAULT_PARTICIPANT_KINDS
from livekit.agents.metrics.usage import ModelUsage
from livekit.agents.types import ATTRIBUTE_PUBLISH_ON_BEHALF
from livekit.agents.voice.room_io import AudioInputOptions, RoomOptions

log = logging.getLogger("dafter.runtime.listeners")

Heard = Callable[[str, str, lk_llm.MetricsReport], None]
Joined = Callable[[str, AgentSession[Any]], None]


class Listener(Agent):
    def __init__(self, speaker: str, heard: Heard) -> None:
        super().__init__(instructions="")
        self._speaker = speaker
        self._heard = heard

    async def on_user_turn_completed(
        self, turn_ctx: lk_llm.ChatContext, new_message: lk_llm.ChatMessage
    ) -> None:
        text = (new_message.text_content or "").strip()
        if text:
            self._heard(self._speaker, text, new_message.metrics)
        raise StopResponse()


def is_human(participant: rtc.RemoteParticipant, room: rtc.Room) -> bool:
    if participant.kind not in DEFAULT_PARTICIPANT_KINDS:
        return False
    behalf = participant.attributes.get(ATTRIBUTE_PUBLISH_ON_BEHALF)
    return behalf != room.local_participant.identity


def is_worker(participant: rtc.RemoteParticipant | None) -> bool:
    return participant is None or participant.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT


def listener_session(
    stt: lk_stt.STT[Any], vad: lk_vad.VAD | None, turn_handling: dict[str, Any]
) -> AgentSession[Any]:
    return AgentSession(
        stt=stt,
        vad=vad,
        turn_handling={  # type: ignore[arg-type]
            **turn_handling,
            "preemptive_generation": {"enabled": False},
        },
        user_away_timeout=None,
    )


def listener_options(identity: str, sample_rate: int) -> RoomOptions:
    return RoomOptions(
        participant_identity=identity,
        audio_input=AudioInputOptions(sample_rate=sample_rate),
        audio_output=False,
        text_input=False,
        close_on_disconnect=False,
    )


class Listeners:
    def __init__(
        self,
        room: rtc.Room,
        new_session: Callable[[], AgentSession[Any]],
        sample_rate: int,
        heard: Heard,
        joined: Joined,
    ) -> None:
        self._room = room
        self._new_session = new_session
        self._sample_rate = sample_rate
        self._heard = heard
        self._joined = joined
        self._sessions: dict[str, AgentSession[Any]] = {}
        self._spent: list[ModelUsage] = []

    def __contains__(self, identity: str) -> bool:
        return identity in self._sessions

    async def join(self, identity: str) -> None:
        if identity in self._sessions:
            return
        session = self._new_session()
        self._sessions[identity] = session
        self._joined(identity, session)
        await session.start(
            agent=Listener(identity, self._heard),
            room=self._room,
            room_options=listener_options(identity, self._sample_rate),
            record=False,
        )
        log.info("listening to participant", extra={"participant": identity})

    async def leave(self, identity: str) -> None:
        session = self._sessions.pop(identity, None)
        if session is None:
            return
        await session.aclose()
        self._spent.extend(session.usage.model_usage)
        log.info("stopped listening to participant", extra={"participant": identity})

    async def aclose(self) -> None:
        for identity in list(self._sessions):
            await self.leave(identity)

    def usage(self) -> list[ModelUsage]:
        live = [u for s in self._sessions.values() for u in s.usage.model_usage]
        return [*self._spent, *live]


__all__ = [
    "Listener",
    "Listeners",
    "is_human",
    "is_worker",
    "listener_options",
    "listener_session",
]
