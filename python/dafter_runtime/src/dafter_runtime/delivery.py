from __future__ import annotations

import asyncio
import contextlib
import logging
import weakref
from collections.abc import AsyncIterable, AsyncIterator, Callable
from typing import Any

from dafter_core.enums import Situation
from dafter_core.speech import Fillers, PhrasesByLanguage, Situations, Speech, every_phrase
from livekit import rtc
from livekit.agents import AgentSession, llm
from livekit.agents import tts as lk_tts
from livekit.agents.voice.events import AgentStateChangedEvent

from .naming import words
from .personas import base_language

log = logging.getLogger("dafter.runtime.delivery")

Phrase = tuple[str, ...]


def phrases(by_language: PhrasesByLanguage) -> frozenset[Phrase]:
    return frozenset(p for p in (words(text) for text in every_phrase(by_language)) if p)


def says(said: Phrase, cues: frozenset[Phrase]) -> bool:
    return any(
        said[i : i + len(cue)] == cue for cue in cues for i in range(len(said) - len(cue) + 1)
    )


class Situational:
    def __init__(self, situations: Situations) -> None:
        self._enabled = situations.enabled
        self._concern = phrases(situations.cues.get(Situation.CONCERN, {}))
        self._greeting = phrases(situations.cues.get(Situation.GREETING, {}))

    def opening(self) -> Situation:
        return Situation.GREETING if self._enabled else Situation.NEUTRAL

    def of(self, heard: str | None) -> Situation:
        if not self._enabled or heard is None:
            return Situation.NEUTRAL
        said = words(heard)
        if says(said, self._concern):
            return Situation.CONCERN
        if says(said, self._greeting):
            return Situation.GREETING
        return Situation.NEUTRAL


def last_heard(chat_ctx: llm.ChatContext) -> str | None:
    for item in reversed(chat_ctx.items):
        if isinstance(item, llm.ChatMessage) and item.role == "user":
            return item.text_content or ""
    return None


Frames = list[rtc.AudioFrame]


class Filler:
    def __init__(self, fillers: Fillers, language: str) -> None:
        self._fillers = fillers
        self._phrases = self._of(language)
        self.after = fillers.after_ms / 1000
        self._audio: dict[str, Frames] = {}
        self._turn = 0
        self._due = asyncio.Event()
        self._timer: asyncio.TimerHandle | None = None
        self._played: weakref.WeakSet[Any] = weakref.WeakSet()
        self._preparing: asyncio.Future[None] | None = None

    def _of(self, language: str) -> tuple[str, ...]:
        if not self._fillers.enabled:
            return ()
        return tuple(self._fillers.phrases.get(base_language(language), ()))

    @property
    def enabled(self) -> bool:
        return bool(self._phrases)

    def speak_in(self, language: str, tts: lk_tts.TTS[Any]) -> None:
        self._phrases = self._of(language)
        self._turn = 0
        missing = tuple(p for p in self._phrases if p not in self._audio)
        if missing:
            self._preparing = asyncio.ensure_future(self.prepare(tts, missing))

    def phrase(self) -> str | None:
        if not self._phrases:
            return None
        phrase = self._phrases[self._turn % len(self._phrases)]
        self._turn += 1
        return phrase

    async def prepare(self, tts: lk_tts.TTS[Any], phrases: tuple[str, ...] = ()) -> None:
        for phrase in phrases or self._phrases:
            try:
                self._audio[phrase] = [ev.frame async for ev in tts.synthesize(phrase)]
            except Exception:
                log.warning("a filler could not be synthesized, it will not play")

    def start(self, session: AgentSession[Any], tts: lk_tts.TTS[Any]) -> None:
        self.follow(session)
        if self.enabled:
            self._preparing = asyncio.ensure_future(self.prepare(tts))

    def follow(self, session: AgentSession[Any]) -> None:
        loop = asyncio.get_running_loop()

        def state_changed(ev: AgentStateChangedEvent) -> None:
            self._hush()
            if ev.new_state == "thinking" and self.enabled:
                self._timer = loop.call_later(self.after, self._due.set)

        session.on("agent_state_changed", state_changed)

    def _hush(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._due.clear()

    def took(self, speech: object) -> bool:
        return speech is not None and speech in self._played

    async def ahead(
        self, reply: AsyncIterable[rtc.AudioFrame], speech: Callable[[], object]
    ) -> AsyncIterator[rtc.AudioFrame]:
        frames: asyncio.Queue[rtc.AudioFrame | None] = asyncio.Queue()

        async def pump() -> None:
            try:
                async for frame in reply:
                    frames.put_nowait(frame)
            finally:
                frames.put_nowait(None)

        pumping = asyncio.ensure_future(pump())
        getting = asyncio.ensure_future(frames.get())
        try:
            if await self._due_first(getting):
                for filler in self._filler(speech):
                    yield filler
            played = await getting
            while played is not None:
                yield played
                played = await frames.get()
            await pumping
        finally:
            for task in (getting, pumping):
                if not task.done():
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    async def _due_first(self, getting: asyncio.Future[rtc.AudioFrame | None]) -> bool:
        due: asyncio.Future[Any] = asyncio.ensure_future(self._due.wait())
        waiting: set[asyncio.Future[Any]] = {getting, due}
        try:
            await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
        finally:
            due.cancel()
        if getting.done():
            return False
        self._due.clear()
        return True

    def _filler(self, speech: Callable[[], object]) -> Frames:
        audio = self._audio.get(self.phrase() or "", [])
        if audio and (current := speech()) is not None:
            self._played.add(current)
        if audio:
            log.info("a slow reply got a filler")
        return audio


class Delivery:
    def __init__(self, speech: Speech, language: str) -> None:
        self._situational = Situational(speech.situations)
        self.filler = Filler(speech.fillers, language)
        self.situation = self._situational.opening()

    def heard(self, chat_ctx: llm.ChatContext) -> None:
        self.situation = self._situational.of(last_heard(chat_ctx))


__all__ = ["Delivery", "Filler", "Situational", "last_heard"]
