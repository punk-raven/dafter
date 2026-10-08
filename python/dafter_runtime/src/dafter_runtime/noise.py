from __future__ import annotations

from collections.abc import Iterable

from .naming import STOPS, words

FILLERS = frozenset(
    {
        "uh",
        "uhh",
        "um",
        "umm",
        "er",
        "erm",
        "ah",
        "eh",
        "huh",
        "anyway",
        "anyways",
        "उम्म",
        "अं",
        "आं",
        "ಅಂ",
        "అం",
    }
)

Phrases = frozenset[tuple[str, ...]]


def stray(word: str) -> bool:
    return len(word) <= 1 or word in FILLERS


class Meaning:
    def __init__(self, acknowledgements: Iterable[str] = ()) -> None:
        self._phrases: Phrases = frozenset(p for p in (words(a) for a in acknowledgements) if p)

    def words(self, text: str) -> tuple[str, ...]:
        return self._parse(text)[0]

    def stop(self, text: str) -> bool:
        return words(text) in STOPS

    def acknowledges(self, text: str) -> bool:
        kept, acknowledged = self._parse(text)
        return acknowledged and not kept

    def _parse(self, text: str) -> tuple[tuple[str, ...], bool]:
        said = words(text)
        kept: list[str] = []
        acknowledged = False
        at = 0
        while at < len(said):
            phrase = self._phrase_at(said, at)
            if phrase:
                acknowledged = True
                at += phrase
                continue
            if not stray(said[at]):
                kept.append(said[at])
            at += 1
        return tuple(kept), acknowledged

    def _phrase_at(self, said: tuple[str, ...], at: int) -> int:
        longest = 0
        for phrase in self._phrases:
            if len(phrase) > longest and said[at : at + len(phrase)] == phrase:
                longest = len(phrase)
        return longest


__all__ = ["FILLERS", "Meaning", "stray"]
