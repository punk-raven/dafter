from __future__ import annotations

from dafter_core.enums import Situation
from dafter_core.speech import PhrasesByLanguage, Situations, Speech, every_phrase
from livekit.agents import llm

from .naming import words

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


class Delivery:
    def __init__(self, speech: Speech) -> None:
        self._situational = Situational(speech.situations)
        self.situation = self._situational.opening()

    def heard(self, chat_ctx: llm.ChatContext) -> None:
        self.situation = self._situational.of(last_heard(chat_ctx))


__all__ = ["Delivery", "Situational", "last_heard"]
