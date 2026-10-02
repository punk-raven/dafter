from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from dafter_core.config import Agent

from . import vocabulary

ZERO_WIDTH = frozenset("\u200b\u200c\u200d\u2060\ufeff")
JOINERS = frozenset("'\u2019\u02bc")
BREAKS = frozenset(",.?!;:\u0964\u0965\u2026\u060c\uff0c\u3002\uff1f\uff01\u2013\u2014")
MAX_LEADING_PARTICLES = 2
FUZZY_MIN_LENGTH = 5


class Heard(StrEnum):
    CALLED = "called"
    STOPPED = "stopped"
    STOP = "stop"
    ASIDE = "aside"


@dataclass(frozen=True, slots=True)
class Token:
    word: str
    break_before: bool
    break_after: bool


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", text) if c not in ZERO_WIDTH).casefold()


def tokens(text: str) -> list[Token]:
    words: list[str] = []
    breaks: list[bool] = [True]
    word: list[str] = []
    for c in fold(text):
        if unicodedata.category(c)[0] in "LMN":
            word.append(c)
            continue
        if c in JOINERS and word:
            continue
        if word:
            words.append("".join(word))
            breaks.append(False)
            word = []
        if c in BREAKS:
            breaks[-1] = True
    if word:
        words.append("".join(word))
        breaks.append(False)
    breaks[-1] = True
    return [
        Token(w, break_before=breaks[i], break_after=breaks[i + 1]) for i, w in enumerate(words)
    ]


def words(text: str) -> tuple[str, ...]:
    return tuple(t.word for t in tokens(text))


PARTICLES = frozenset(w for p in vocabulary.PARTICLES for w in words(p))
FOLLOWERS = frozenset(w for p in vocabulary.FOLLOWERS for w in words(p))
STOPS = frozenset(words(p) for p in vocabulary.STOPS)


def within_one_edit(a: str, b: str) -> bool:
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    if len(a) == len(b):
        return a[i + 1 :] == b[i + 1 :]
    return a[i:] == b[i + 1 :]


class Matcher:
    def __init__(self, name: str, aliases: tuple[str, ...], near_misses: tuple[str, ...]) -> None:
        spellings = {words(n) for n in (name, *aliases)}
        self._names = sorted((s for s in spellings if s), key=len, reverse=True)
        self._single = {s[0] for s in self._names if len(s) == 1}
        self._near_misses = {w for m in near_misses for w in words(m)}

    @classmethod
    def for_agent(cls, agent: Agent) -> Matcher:
        addressing = agent.addressing
        return cls(agent.name or "", addressing.aliases, addressing.near_misses)

    def _is_name_word(self, word: str) -> bool:
        if word in self._single:
            return True
        if word in self._near_misses or len(word) < FUZZY_MIN_LENGTH:
            return False
        return any(within_one_edit(word, n) for n in self._single)

    def _name_at(self, heard: list[Token], i: int) -> int:
        for spelling in self._names:
            if len(spelling) == 1:
                if self._is_name_word(heard[i].word):
                    return 1
                continue
            if tuple(t.word for t in heard[i : i + len(spelling)]) == spelling:
                return len(spelling)
        return 0

    def _spans(self, heard: list[Token]) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        i = 0
        while i < len(heard):
            n = self._name_at(heard, i)
            if n:
                spans.append((i, i + n))
                i += n
            else:
                i += 1
        return spans

    def _vocative(self, heard: list[Token], start: int, end: int) -> bool:
        if heard[start].break_before and heard[end - 1].break_after:
            return True
        leading = [t.word for t in heard[:start]]
        if len(leading) > MAX_LEADING_PARTICLES or any(w not in PARTICLES for w in leading):
            return False
        if heard[end - 1].break_after or end == len(heard):
            return True
        return heard[end].word not in FOLLOWERS

    def _rest(self, heard: list[Token], spans: list[tuple[int, int]]) -> tuple[str, ...]:
        named = {i for start, end in spans for i in range(start, end)}
        return tuple(
            t.word for i, t in enumerate(heard) if i not in named and t.word not in PARTICLES
        )

    def hear(self, text: str) -> Heard:
        heard = tokens(text)
        spans = self._spans(heard)
        rest = self._rest(heard, spans)
        called = any(self._vocative(heard, start, end) for start, end in spans)
        if called:
            return Heard.STOPPED if rest in STOPS else Heard.CALLED
        if not spans and rest in STOPS:
            return Heard.STOP
        return Heard.ASIDE


__all__ = ["Heard", "Matcher", "Token", "fold", "tokens", "within_one_edit", "words"]
