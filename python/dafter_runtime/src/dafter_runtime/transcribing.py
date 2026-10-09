from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

from livekit import rtc
from livekit.agents import JobContext
from livekit.agents.llm import MetricsReport
from livekit.agents.metrics.usage import ModelUsage

from .called import listening
from .captions import Captions
from .listeners import Listeners, is_human
from .plan import Plan
from .stages import Stages, filtered_input


class Transcribing:
    def __init__(
        self, ctx: JobContext, p: Plan, stages: Stages, captions: Captions, sample_rate: int
    ) -> None:
        self._ctx = ctx
        self._captions = captions
        self.listeners = Listeners(
            ctx.room,
            listening(p, stages),
            filtered_input(p, sample_rate),
            self._heard,
            captions.follow,
        )
        self._tasks: set[asyncio.Task[None]] = set()

    def _heard(self, speaker: str, text: str, timing: MetricsReport) -> None:
        return None

    def usage(self) -> list[ModelUsage]:
        return self.listeners.usage()

    def _spawn(self, work: Coroutine[Any, Any, None]) -> None:
        task = asyncio.ensure_future(work)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _joined(self, participant: rtc.RemoteParticipant) -> None:
        if is_human(participant, self._ctx.room):
            self._spawn(self.listeners.join(participant.identity))

    def _left(self, participant: rtc.RemoteParticipant) -> None:
        if is_human(participant, self._ctx.room):
            self._captions.left(participant.identity)
            self._spawn(self.listeners.leave(participant.identity))

    def listen(self) -> None:
        self._ctx.room.on("participant_connected", self._joined)
        self._ctx.room.on("participant_disconnected", self._left)
        for participant in self._ctx.room.remote_participants.values():
            self._joined(participant)
        self._ctx.add_shutdown_callback(self.listeners.aclose)


__all__ = ["Transcribing"]
