from __future__ import annotations

from collections.abc import AsyncIterable, AsyncIterator, Callable, Iterable
from typing import Any

from dafter_core.speech import Backchannel, every_phrase
from livekit.agents import AgentSession, stt

from .naming import words

Floor = Callable[[], bool]
Events = AsyncIterable[stt.SpeechEvent | str]
Filter = Callable[[Events], Events]

TRANSCRIPTS = frozenset(
    {
        stt.SpeechEventType.INTERIM_TRANSCRIPT,
        stt.SpeechEventType.PREFLIGHT_TRANSCRIPT,
        stt.SpeechEventType.FINAL_TRANSCRIPT,
    }
)


class Acknowledgements:
    def __init__(self, phrases: Iterable[str], max_words: int) -> None:
        self._phrases = frozenset(p for p in (words(text) for text in phrases) if p)
        self._max_words = max_words

    @classmethod
    def of(cls, backchannel: Backchannel) -> Acknowledgements | None:
        phrases = every_phrase(backchannel.words)
        if not backchannel.enabled or not phrases:
            return None
        return cls(phrases, backchannel.max_words)

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
        self._passing = False

    def feed(self, event: stt.SpeechEvent) -> list[stt.SpeechEvent]:
        kind = event.type
        if kind is stt.SpeechEventType.START_OF_SPEECH:
            self._reset()
            self._doubt = self._floor()
            self._passing = not self._doubt
            return self._hold(event) if self._doubt else [event]
        if kind in TRANSCRIPTS:
            return self._transcript(event)
        if kind is stt.SpeechEventType.END_OF_SPEECH:
            if not self._doubt:
                self._passing = False
                return [event]
            if self._floor():
                acknowledged = bool(self._finals)
                self._reset()
                if acknowledged and self._dropped is not None:
                    self._dropped()
                return []
            return self._release(event)
        return [event]

    def _transcript(self, event: stt.SpeechEvent) -> list[stt.SpeechEvent]:
        if not self._doubt:
            if self._passing or not self._floor():
                return [event]
            self._doubt = True
        final = event.type is stt.SpeechEventType.FINAL_TRANSCRIPT
        said = " ".join([*self._finals, text_of(event)])
        if not self._floor() or not self._acknowledgements.only(said, finished=final):
            return self._release(event)
        if final:
            self._finals.append(text_of(event))
        return self._hold(event)

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


async def sieved(events: Events, sieve: Sieve) -> AsyncIterator[stt.SpeechEvent | str]:
    async for event in events:
        if isinstance(event, str):
            yield event
            continue
        for passed in sieve.feed(event):
            yield passed


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
    "Sieve",
    "acknowledged",
    "holds_floor",
    "sieved",
    "text_of",
]
