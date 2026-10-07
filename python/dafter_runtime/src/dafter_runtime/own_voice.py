from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import AsyncIterable, AsyncIterator, Callable
from dataclasses import dataclass
from typing import Any

from livekit.agents import AgentSession, stt
from livekit.agents.voice.events import AgentStateChangedEvent, UserStateChangedEvent

from .addressing import Clock
from .backchannel import TRANSCRIPTS, Events, Filter, text_of
from .naming import STOPS, within_one_edit, words

log = logging.getLogger("dafter.runtime.own_voice")

KEPT_S = 20.0
KEPT_SPEECHES = 3
ONSET_GRACE_S = 0.5
SHARE = 0.6
NEW_WORDS = 3
STEM = 4
PREFIX = 3
ECHO_WORDS = 2

Words = tuple[str, ...]


def recognized_as(heard: str, spoken: str) -> bool:
    if heard == spoken or (len(heard) >= PREFIX and spoken.startswith(heard)):
        return True
    if len(spoken) >= STEM and heard.startswith(spoken):
        return True
    return min(len(heard), len(spoken)) >= STEM and within_one_edit(heard, spoken)


def missed(heard: Words, said: frozenset[str]) -> int:
    return sum(1 for w in heard if w not in said and not any(recognized_as(w, s) for s in said))


@dataclass(slots=True)
class Spoken:
    at: float
    words: Words = ()


@dataclass(slots=True)
class Stretch:
    began: float
    ended: float | None = None


class OwnVoice:
    def __init__(self, clock: Clock = time.time) -> None:
        self.clock = clock
        self._said: deque[Spoken] = deque()
        self._stretches: deque[Stretch] = deque()

    def follow(self, session: AgentSession[Any]) -> None:
        def changed(ev: AgentStateChangedEvent) -> None:
            if ev.new_state == "speaking":
                self.speaking()
            elif ev.old_state == "speaking":
                self.quiet()

        session.on("agent_state_changed", changed)

    def speaking(self) -> None:
        if not self._stretches or self._stretches[-1].ended is not None:
            self._stretches.append(Stretch(self.clock()))

    def quiet(self) -> None:
        if self._stretches and self._stretches[-1].ended is None:
            self._stretches[-1].ended = self.clock()

    def said(self, text: str) -> None:
        if spoken := words(text):
            self._said.append(Spoken(self.clock(), spoken))

    async def saying(self, text: AsyncIterable[str]) -> AsyncIterator[str]:
        spoken = Spoken(self.clock())
        self._said.append(spoken)
        so_far = ""
        async for chunk in text:
            so_far += chunk
            spoken.words = words(so_far)
            yield chunk

    def echoes(self, text: str, onset: float | None, echoing: bool = False) -> bool:
        now = self.clock()
        self._forget(now)
        heard = words(text)
        if not heard or onset is None or not self._speaking_at(onset, now):
            return False
        new = missed(heard, frozenset(w for s in self._said for w in s.words))
        if heard in STOPS and new == len(heard):
            return False
        return new <= (1 - SHARE) * len(heard) or (echoing and new < NEW_WORDS)

    def stretch_at(self, onset: float | None) -> Stretch | None:
        if onset is None:
            return None
        now = self.clock()
        return next(
            (
                s
                for s in self._stretches
                if s.began <= onset <= (now if s.ended is None else s.ended) + ONSET_GRACE_S
            ),
            None,
        )

    def hearing(self, session: AgentSession[Any], echoed: Callable[[], None]) -> Filter:
        return Ear(self, session, echoed)

    def _speaking_at(self, onset: float, now: float) -> bool:
        return self.stretch_at(onset) is not None

    def _forget(self, now: float) -> None:
        while len(self._said) > KEPT_SPEECHES and now - self._said[0].at > KEPT_S:
            self._said.popleft()
        while self._stretches and (ended := self._stretches[0].ended) and now - ended > KEPT_S:
            self._stretches.popleft()


def wait_for_words(session: AgentSession[Any]) -> None:
    interruption = session.options.interruption
    if interruption.get("min_words", 0) < 1:
        interruption["min_words"] = 1


class Ear:
    def __init__(
        self, voice: OwnVoice, session: AgentSession[Any], echoed: Callable[[], None]
    ) -> None:
        self._voice = voice
        self._echoed = echoed
        self._clock = voice.clock
        self._onset: float | None = None
        self._vad = False
        self._echo_in: Stretch | None = None
        self._reported = False
        session.on("user_state_changed", self._user_state)

    def _user_state(self, ev: UserStateChangedEvent) -> None:
        if ev.new_state == "speaking":
            self._vad = True
            self._onset = self._clock()

    def __call__(self, events: Events) -> Events:
        return self._sieve(events)

    async def _sieve(self, events: Events) -> AsyncIterator[stt.SpeechEvent | str]:
        async for event in events:
            if isinstance(event, stt.SpeechEvent):
                if event.type is stt.SpeechEventType.START_OF_SPEECH and not self._vad:
                    self._onset = self._clock()
                if event.type in TRANSCRIPTS and self._echoes(text_of(event)):
                    continue
            yield event

    def _echoes(self, text: str) -> bool:
        stretch = self._voice.stretch_at(self._onset)
        echoing = stretch is not None and stretch is self._echo_in
        if not self._voice.echoes(text, self._onset, echoing):
            return False
        if not echoing and len(words(text)) >= ECHO_WORDS:
            self._echo_in = stretch
            log.info("a microphone returns the agent's voice, its echo is ignored this reply")
            if not self._reported:
                self._reported = True
                self._echoed()
        return True


__all__ = ["Ear", "OwnVoice", "missed", "recognized_as", "wait_for_words"]
