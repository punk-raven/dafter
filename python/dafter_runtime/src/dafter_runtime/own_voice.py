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
from .naming import within_one_edit, words

log = logging.getLogger("dafter.runtime.own_voice")

KEPT_S = 20.0
KEPT_SPEECHES = 3
ONSET_GRACE_S = 0.5
SHARE = 0.6
NEW_WORDS = 3
STEM = 4

Words = tuple[str, ...]


def recognized_as(heard: str, spoken: str) -> bool:
    if spoken.startswith(heard) or (len(spoken) >= STEM and heard.startswith(spoken)):
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
        return new <= (1 - SHARE) * len(heard) or (echoing and new < NEW_WORDS)

    def hearing(self, session: AgentSession[Any], echoed: Callable[[], None]) -> Filter:
        return Ear(self, session, echoed)

    def _speaking_at(self, onset: float, now: float) -> bool:
        return any(
            s.began <= onset <= (now if s.ended is None else s.ended) + ONSET_GRACE_S
            for s in self._stretches
        )

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
        self._echoing = False
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
        if not self._voice.echoes(text, self._onset, self._echoing):
            return False
        if not self._echoing:
            log.info("a microphone returns the agent's own voice, it is no longer heard")
            self._echoing = True
            self._echoed()
        return True


__all__ = ["Ear", "OwnVoice", "missed", "recognized_as", "wait_for_words"]
