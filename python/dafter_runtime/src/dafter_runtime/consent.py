from __future__ import annotations

from . import vocabulary
from .naming import PARTICLES, words

AFFIRMATIVES = frozenset(words(p) for p in vocabulary.AFFIRMATIVES)
NEGATIVES = frozenset(w for p in vocabulary.NEGATIVES for w in words(p))
LONGEST_AFFIRMATIVE = max(len(a) for a in AFFIRMATIVES)

Request = tuple[str, str]


def answer(text: str, name_words: frozenset[str] = frozenset()) -> bool | None:
    said = [w for w in words(text) if w not in PARTICLES and w not in name_words]
    if any(w in NEGATIVES for w in said):
        return False
    for n in range(1, LONGEST_AFFIRMATIVE + 1):
        if tuple(said[:n]) in AFFIRMATIVES:
            return True
    return None


class Confirmations:
    def __init__(self, name_words: frozenset[str] = frozenset()) -> None:
        self._name_words = name_words
        self._asked: dict[str, Request] = {}
        self._given: set[tuple[str, str, str]] = set()

    def ask(self, caller: str, tool: str, arguments: str) -> None:
        self._asked[caller] = (tool, arguments)
        self._given = {g for g in self._given if g[0] != caller}

    def heard(self, speaker: str, text: str) -> None:
        asked = self._asked.get(speaker)
        if asked is None:
            return
        verdict = answer(text, self._name_words)
        if verdict is None:
            return
        del self._asked[speaker]
        if verdict:
            self._given.add((speaker, *asked))

    def take(self, caller: str, tool: str, arguments: str) -> bool:
        given = (caller, tool, arguments)
        if given not in self._given:
            return False
        self._given.discard(given)
        return True


__all__ = ["Confirmations", "answer"]
