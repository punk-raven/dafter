from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Callable, Mapping

from dafter_core.switching import LanguageSwitching
from livekit.agents import stt

from .backchannel import Events, Filter
from .naming import words
from .personas import Persona, base_language

log = logging.getLogger("dafter.runtime.switching")

Follower = Callable[[str], None]
CONFIDENCE = "language_confidence"


class Switching:
    def __init__(
        self, config: LanguageSwitching, language: str, personas: Mapping[str, Persona]
    ) -> None:
        self.language = language
        self.enabled = config.enabled
        self._tags = {base_language(tag): tag for tag in config.languages} if self.enabled else {}
        self._min_confidence = config.min_confidence
        self._min_words = config.min_words
        self._personas = dict(personas)
        self._asked = False
        self._heard: dict[str, str] = {}
        self._followers: list[Follower] = []

    @property
    def languages(self) -> list[str]:
        return list(self._tags.values())

    @property
    def persona(self) -> Persona:
        return self._personas[self.language]

    def follow_with(self, follower: Follower) -> None:
        self._followers.append(follower)

    def identified(self, event: stt.SpeechEvent) -> str | None:
        if event.type is not stt.SpeechEventType.FINAL_TRANSCRIPT or not event.alternatives:
            return None
        said = event.alternatives[0]
        tag = self._tags.get(base_language(str(said.language)))
        if tag is None or len(words(said.text)) < self._min_words:
            return None
        confidence = (said.metadata or {}).get(CONFIDENCE)
        if isinstance(confidence, bool) or not isinstance(confidence, int | float):
            return tag
        return tag if confidence >= self._min_confidence else None

    def heard(self, speaker: str | None, event: stt.SpeechEvent) -> None:
        tag = self.identified(event)
        if tag is None:
            return
        if speaker is None:
            self.follow(tag)
        else:
            self._heard[speaker] = tag

    def answering(self, speaker: str) -> None:
        if (tag := self._heard.get(speaker)) is not None:
            self.follow(tag)

    def follow(self, tag: str) -> None:
        if not self._asked:
            self._switch(tag, "identified")

    def ask(self, tag: str) -> bool:
        if tag not in self._tags.values():
            return False
        self._asked = True
        self._switch(tag, "asked")
        return True

    def _switch(self, tag: str, via: str) -> None:
        if tag == self.language:
            return
        log.info("language switched", extra={"from": self.language, "to": tag, "via": via})
        self.language = tag
        for follower in self._followers:
            follower(tag)

    def observe(self, speaker: str | None = None) -> Filter:
        def watched(events: Events) -> Events:
            async def watching() -> AsyncIterator[stt.SpeechEvent | str]:
                async for event in events:
                    if isinstance(event, stt.SpeechEvent):
                        self.heard(speaker, event)
                    yield event

            return watching()

        return watched


__all__ = ["Switching"]
