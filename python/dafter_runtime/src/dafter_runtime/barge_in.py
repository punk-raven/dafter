from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from livekit.agents import AgentSession
from livekit.agents.tokenize.basic import split_words
from livekit.agents.voice.events import UserInputTranscribedEvent, UserStateChangedEvent

from .addressing import Clock, Schedule, Timer
from .noise import Meaning

log = logging.getLogger("dafter.runtime.barge_in")


@dataclass(frozen=True, slots=True)
class Resume:
    pause: Callable[[], bool]
    resume: Callable[[], None]
    after: float


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
        resume: Resume | None = None,
        waits_for_words: bool = False,
        meaning: Meaning | None = None,
    ) -> None:
        self._min_duration = min_duration
        self._meaning = meaning
        self._min_words = max(min_words, 1 if waits_for_words or resume else 0)
        self._caller = caller
        self._stop = stop
        self._clock = clock
        self._schedule = schedule
        self._resume = resume
        self._hearing: dict[str, Hearing] = {}
        self._echoing: set[str] = set()
        self._paused = False
        self._resuming: Timer | None = None

    def speaking(self, speaker: str, since: float) -> None:
        hearing = self._hearing.setdefault(speaker, Hearing())
        hearing.hush()
        if speaker == self._caller():
            self._hold_resume()
        held = max(0.0, self._min_duration - (self._clock() - since))
        hearing.timer = self._schedule(held, lambda: self._held(speaker))

    def quiet(self, speaker: str) -> None:
        if (hearing := self._hearing.get(speaker)) is not None:
            hearing.hush()
        if self._paused and speaker == self._caller() and self._resume is not None:
            self._hold_resume()
            self._resuming = self._schedule(self._resume.after, self._resumed)

    def transcribed(self, speaker: str, text: str, final: bool) -> None:
        hearing = self._hearing.setdefault(speaker, Hearing())
        if final:
            hearing.final = " ".join(t for t in (hearing.final, text) if t)
            hearing.interim = ""
        else:
            hearing.interim = text
        if hearing.sustained or self._paused:
            self._try(speaker, hearing)

    def committed(self, speaker: str) -> None:
        if (hearing := self._hearing.get(speaker)) is not None:
            hearing.final = hearing.interim = ""

    def acknowledged(self, speaker: str) -> None:
        if speaker == self._caller():
            self._hold_resume()
            self._resumed()

    def echoing(self, speaker: str) -> None:
        self._echoing.add(speaker)
        self.acknowledged(speaker)

    def left(self, speaker: str) -> None:
        self._echoing.discard(speaker)
        if (hearing := self._hearing.pop(speaker, None)) is not None:
            hearing.hush()

    def replying(self) -> None:
        caller = self._caller()
        if caller is not None and (hearing := self._hearing.get(caller)) and hearing.sustained:
            self._pause(caller)
            self._try(caller, hearing)

    def _held(self, speaker: str) -> None:
        hearing = self._hearing.get(speaker)
        if hearing is None:
            return
        hearing.timer = None
        hearing.sustained = True
        if speaker == self._caller():
            self._pause(speaker)
        self._try(speaker, hearing)

    def _pause(self, speaker: str) -> None:
        if speaker in self._echoing:
            return
        if self._resume is not None and self._resume.pause():
            self._paused = True
            log.info("the agent paused for a voice over it", extra={"participant": speaker})

    def _hold_resume(self) -> None:
        if self._resuming is not None:
            self._resuming.cancel()
            self._resuming = None

    def _resumed(self) -> None:
        self._resuming = None
        if self._paused and self._resume is not None:
            self._paused = False
            self._resume.resume()
            log.info("the agent resumed: no words came")

    def _try(self, speaker: str, hearing: Hearing) -> None:
        if speaker != self._caller():
            return
        transcript = hearing.transcript()
        needed = max(self._min_words, 1) if speaker in self._echoing else self._min_words
        stopped = self._meaning is not None and self._meaning.stop(transcript)
        if needed > 0 and not stopped and self._said(transcript) < needed:
            return
        self._hold_resume()
        self._paused = False
        self._stop()

    def _said(self, transcript: str) -> int:
        if self._meaning is not None:
            return len(self._meaning.words(transcript))
        return len(split_words(transcript, split_character=True))


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


__all__ = ["BargeIn", "Resume", "follow"]
