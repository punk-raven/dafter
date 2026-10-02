from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from livekit.agents import AgentSession
from livekit.agents.tokenize.basic import split_words
from livekit.agents.voice.events import UserInputTranscribedEvent, UserStateChangedEvent

from .addressing import Clock, Schedule, Timer


@dataclass(slots=True)
class Hearing:
    timer: Timer | None = None
    sustained: bool = False
    final: str = ""
    interim: str = ""

    def transcript(self) -> str:
        return " ".join(t for t in (self.final, self.interim) if t)

    def hush(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            self.timer = None
        self.sustained = False


class BargeIn:
    def __init__(
        self,
        min_duration: float,
        min_words: int,
        caller: Callable[[], str | None],
        stop: Callable[[], None],
        clock: Clock,
        schedule: Schedule,
    ) -> None:
        self._min_duration = min_duration
        self._min_words = min_words
        self._caller = caller
        self._stop = stop
        self._clock = clock
        self._schedule = schedule
        self._hearing: dict[str, Hearing] = {}

    def speaking(self, speaker: str, since: float) -> None:
        hearing = self._hearing.setdefault(speaker, Hearing())
        hearing.hush()
        held = max(0.0, self._min_duration - (self._clock() - since))
        hearing.timer = self._schedule(held, lambda: self._held(speaker))

    def quiet(self, speaker: str) -> None:
        if (hearing := self._hearing.get(speaker)) is not None:
            hearing.hush()

    def transcribed(self, speaker: str, text: str, final: bool) -> None:
        hearing = self._hearing.setdefault(speaker, Hearing())
        if final:
            hearing.final = " ".join(t for t in (hearing.final, text) if t)
            hearing.interim = ""
        else:
            hearing.interim = text
        if hearing.sustained:
            self._try(speaker, hearing)

    def committed(self, speaker: str) -> None:
        if (hearing := self._hearing.get(speaker)) is not None:
            hearing.final = hearing.interim = ""

    def left(self, speaker: str) -> None:
        if (hearing := self._hearing.pop(speaker, None)) is not None:
            hearing.hush()

    def replying(self) -> None:
        caller = self._caller()
        if caller is not None and (hearing := self._hearing.get(caller)) and hearing.sustained:
            self._try(caller, hearing)

    def _held(self, speaker: str) -> None:
        hearing = self._hearing.get(speaker)
        if hearing is None:
            return
        hearing.timer = None
        hearing.sustained = True
        self._try(speaker, hearing)

    def _try(self, speaker: str, hearing: Hearing) -> None:
        if speaker != self._caller():
            return
        if self._min_words > 0:
            if len(split_words(hearing.transcript(), split_character=True)) < self._min_words:
                return
        self._stop()


def follow(barge_in: BargeIn, speaker: str, session: AgentSession[Any]) -> None:
    def state_changed(ev: UserStateChangedEvent) -> None:
        if ev.new_state == "speaking":
            barge_in.speaking(speaker, ev.created_at)
        else:
            barge_in.quiet(speaker)

    def transcribed(ev: UserInputTranscribedEvent) -> None:
        barge_in.transcribed(speaker, ev.transcript, ev.is_final)

    session.on("user_state_changed", state_changed)
    session.on("user_input_transcribed", transcribed)


__all__ = ["BargeIn", "follow"]
