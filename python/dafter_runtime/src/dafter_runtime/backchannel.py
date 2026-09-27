from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, AsyncIterable, AsyncIterator, Callable, Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from dafter_core.speech import Backchannel, every_phrase
from livekit.agents import AgentSession, llm, stt
from livekit.agents.voice.events import AgentStateChangedEvent
from livekit.agents.voice.speech_handle import SpeechHandle

from .naming import words

Clock = Callable[[], float]
Events = AsyncIterable[stt.SpeechEvent | str]
Filter = Callable[[Events], Events]

TRANSCRIPTS = frozenset(
    {
        stt.SpeechEventType.INTERIM_TRANSCRIPT,
        stt.SpeechEventType.PREFLIGHT_TRANSCRIPT,
        stt.SpeechEventType.FINAL_TRANSCRIPT,
    }
)
SPOKEN = TRANSCRIPTS | {stt.SpeechEventType.START_OF_SPEECH, stt.SpeechEventType.END_OF_SPEECH}
QUESTION_MARKS = ("?", "\uff1f")
AFTER_QUESTION = " \t\n\"')]\u00bb\u201d\u2019\u0964\u0965"


class Acknowledgements:
    def __init__(self, phrases: Iterable[str], max_words: int, answer_within: float) -> None:
        self._phrases = frozenset(p for p in (words(text) for text in phrases) if p)
        self._max_words = max_words
        self.answer_within = answer_within

    @classmethod
    def of(cls, backchannel: Backchannel) -> Acknowledgements | None:
        phrases = every_phrase(backchannel.words)
        if not backchannel.enabled or not phrases:
            return None
        return cls(phrases, backchannel.max_words, backchannel.answer_within_ms / 1000)

    def only(self, text: str, finished: bool) -> bool:
        said = words(text)
        if not said or len(said) > self._max_words:
            return False
        return self._covers(said, 0, finished)

    def _covers(self, said: tuple[str, ...], at: int, finished: bool) -> bool:
        if at == len(said):
            return True
        rest = said[at:]
        for phrase in self._phrases:
            if rest[: len(phrase)] == phrase and self._covers(said, at + len(phrase), finished):
                return True
            if not finished and len(rest) <= len(phrase) and _begins(phrase, rest):
                return True
        return False


def _begins(phrase: tuple[str, ...], said: tuple[str, ...]) -> bool:
    last = len(said) - 1
    return phrase[:last] == said[:last] and phrase[last].startswith(said[last])


def text_of(event: stt.SpeechEvent) -> str:
    return event.alternatives[0].text if event.alternatives else ""


def is_question(text: str) -> bool:
    return text.rstrip(AFTER_QUESTION).endswith(QUESTION_MARKS)


def asks(speech: SpeechHandle) -> bool:
    if speech.interrupted:
        return False
    said = [
        item.text_content or ""
        for item in speech.chat_items
        if isinstance(item, llm.ChatMessage) and item.role == "assistant"
    ]
    return bool(said) and is_question(said[-1])


class Reply:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._played = 0.0
        self._since: float | None = None
        self._ended: list[Callable[[bool], None]] = []

    def playing(self, now: bool) -> None:
        if self._since is not None:
            self._played += self._clock() - self._since
        self._since = self._clock() if now else None

    def played(self) -> float:
        running = self._clock() - self._since if self._since is not None else 0.0
        return self._played + running

    def when_over(self, then: Callable[[bool], None]) -> None:
        self._ended.append(then)

    def over(self, asked: bool) -> None:
        self.playing(False)
        ended, self._ended = self._ended, []
        for then in ended:
            then(asked)


class Floor(Protocol):
    def held(self) -> bool: ...

    def heard(self) -> Reply | None: ...


class SessionFloor:
    def __init__(self, session: AgentSession[Any], clock: Clock = time.monotonic) -> None:
        self._session = session
        self._clock = clock
        self._speech: SpeechHandle | None = None
        self._reply: Reply | None = None
        session.on("agent_state_changed", self._changed)
        self._track(session.agent_state == "speaking")

    def held(self) -> bool:
        return holds_floor(self._session)

    def heard(self) -> Reply | None:
        if self._speech is None or self._speech is not self._session.current_speech:
            return None
        return self._reply if self.held() else None

    def _changed(self, ev: AgentStateChangedEvent) -> None:
        self._track(ev.new_state == "speaking")

    def _track(self, speaking: bool) -> None:
        speech = self._session.current_speech
        if speaking and speech is not None and speech is not self._speech:
            if self._reply is not None:
                self._reply.playing(False)
            reply = Reply(self._clock)
            self._speech, self._reply = speech, reply
            speech.add_done_callback(lambda done: reply.over(asks(done)))
        if self._reply is not None:
            self._reply.playing(speaking)


@dataclass(slots=True)
class Pending:
    reply: Reply
    played: float
    events: list[stt.SpeechEvent]


class Sieve:
    def __init__(
        self,
        acknowledgements: Acknowledgements,
        floor: Floor,
        dropped: Callable[[], None] | None = None,
    ) -> None:
        self._acknowledgements = acknowledgements
        self._floor = floor
        self._dropped = dropped
        self._held: list[stt.SpeechEvent] = []
        self._finals: list[str] = []
        self._doubt = False
        self._over: Reply | None = None
        self._passing = False
        self._pending: Pending | None = None
        self._answers: list[stt.SpeechEvent] = []
        self.answering = asyncio.Event()

    def feed(self, event: stt.SpeechEvent) -> list[stt.SpeechEvent]:
        kind = event.type
        if kind in SPOKEN:
            self._pending = None
        if kind is stt.SpeechEventType.START_OF_SPEECH:
            self._reset()
            if self._floor.held():
                self._doubt_over()
            self._passing = not self._doubt
            return self._hold(event) if self._doubt else [event]
        if kind in TRANSCRIPTS:
            return self._transcript(event)
        if kind is stt.SpeechEventType.END_OF_SPEECH:
            if not self._doubt:
                self._passing = False
                return [event]
            if self._floor.held():
                self._drop(event)
                return []
            return self._release(event)
        return [event]

    def answered(self) -> list[stt.SpeechEvent]:
        answers, self._answers = self._answers, []
        self.answering.clear()
        return answers

    def _doubt_over(self) -> None:
        self._doubt = True
        self._over = self._floor.heard()

    def _transcript(self, event: stt.SpeechEvent) -> list[stt.SpeechEvent]:
        if not self._doubt:
            if self._passing or not self._floor.held():
                return [event]
            self._doubt_over()
        final = event.type is stt.SpeechEventType.FINAL_TRANSCRIPT
        said = " ".join([*self._finals, text_of(event)])
        if not self._floor.held() or not self._acknowledgements.only(said, finished=final):
            return self._release(event)
        if final:
            self._finals.append(text_of(event))
        return self._hold(event)

    def _drop(self, event: stt.SpeechEvent) -> None:
        acknowledged = bool(self._finals)
        said = [*self._held, event]
        over = self._over
        self._reset()
        if not acknowledged:
            return
        if self._dropped is not None:
            self._dropped()
        if over is not None and over is self._floor.heard():
            pending = Pending(over, over.played(), said)
            self._pending = pending
            over.when_over(lambda asked: self._answer(pending, asked))

    def _answer(self, pending: Pending, asked: bool) -> None:
        if self._pending is not pending:
            return
        self._pending = None
        left = pending.reply.played() - pending.played
        if asked and left <= self._acknowledgements.answer_within:
            self._answers.extend(pending.events)
            self.answering.set()

    def _hold(self, event: stt.SpeechEvent) -> list[stt.SpeechEvent]:
        self._held.append(event)
        return []

    def _release(self, event: stt.SpeechEvent) -> list[stt.SpeechEvent]:
        released = [*self._held, event]
        self._reset()
        self._passing = event.type is not stt.SpeechEventType.END_OF_SPEECH
        return released

    def _reset(self) -> None:
        self._held = []
        self._finals = []
        self._doubt = False
        self._over = None


async def sieved(events: Events, sieve: Sieve) -> AsyncIterator[stt.SpeechEvent | str]:
    upstream = aiter(events)
    reading: asyncio.Future[stt.SpeechEvent | str] | None = None
    answering: asyncio.Future[Any] | None = None
    try:
        while True:
            if reading is None:
                reading = asyncio.ensure_future(anext(upstream))
            if answering is None:
                answering = asyncio.ensure_future(sieve.answering.wait())
            either: set[asyncio.Future[Any]] = {reading, answering}
            await asyncio.wait(either, return_when=asyncio.FIRST_COMPLETED)
            if answering.done():
                answering = None
            for answer in sieve.answered():
                yield answer
            if not reading.done():
                continue
            done, reading = reading, None
            try:
                event = done.result()
            except StopAsyncIteration:
                return
            if isinstance(event, str):
                yield event
                continue
            for passed in sieve.feed(event):
                yield passed
    finally:
        waiting = [w for w in (reading, answering) if w is not None]
        for waiter in waiting:
            waiter.cancel()
        await asyncio.gather(*waiting, return_exceptions=True)
        if isinstance(upstream, AsyncGenerator):
            await upstream.aclose()


def acknowledged(
    acknowledgements: Acknowledgements | None,
    floor: Floor,
    dropped: Callable[[], None] | None = None,
) -> Filter:
    def apply(events: Events) -> Events:
        if acknowledgements is None:
            return events
        return sieved(events, Sieve(acknowledgements, floor, dropped))

    return apply


def holds_floor(session: AgentSession[Any]) -> bool:
    speech = session.current_speech
    return speech is not None and not speech.done() and not speech.interrupted


__all__ = [
    "Acknowledgements",
    "Events",
    "Filter",
    "Floor",
    "Reply",
    "SessionFloor",
    "Sieve",
    "acknowledged",
    "asks",
    "holds_floor",
    "is_question",
    "sieved",
    "text_of",
]
