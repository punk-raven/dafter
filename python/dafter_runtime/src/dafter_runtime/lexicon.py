from __future__ import annotations

from collections.abc import Callable, Iterable
from enum import StrEnum

from dafter_core.speech import Backchannel, every_phrase

from .naming import words
from .noise import stray

Said = tuple[str, ...]
Phrases = frozenset[Said]
Affirms = Callable[[Said], bool]


class Cue(StrEnum):
    SILENT = "silent"
    AFFIRMATIVE = "affirmative"
    NEGATIVE = "negative"
    CONTINUED = "continued"
    SPEECH = "speech"


YIELDING = frozenset({Cue.NEGATIVE, Cue.CONTINUED})


def phrases(texts: Iterable[str]) -> Phrases:
    return frozenset(p for p in (words(text) for text in texts) if p)


def _contains(said: Said, phrase: Said) -> bool:
    size = len(phrase)
    return any(said[at : at + size] == phrase for at in range(len(said) - size + 1))


def _real(said: Said) -> bool:
    return any(not stray(word) for word in said)


class Lexicon:
    def __init__(self, affirmatives: Iterable[str], negatives: Iterable[str]) -> None:
        self.affirmatives = phrases(affirmatives)
        self.negatives = phrases(negatives)

    @classmethod
    def of(cls, backchannel: Backchannel) -> Lexicon:
        return cls(every_phrase(backchannel.words), every_phrase(backchannel.negatives))

    def negates(self, said: Said) -> bool:
        return any(_contains(said, phrase) for phrase in self.negatives)

    def affirms(self, said: Said) -> bool:
        at = 0
        affirmed = False
        while at < len(said):
            longest = self._affirmative_at(said, at)
            if longest:
                affirmed = True
                at += longest
            elif stray(said[at]):
                at += 1
            else:
                return False
        return affirmed

    def cue(self, said: Said, affirms_too: Affirms | None = None) -> Cue:
        def affirmed(part: Said) -> bool:
            return self.affirms(part) or (affirms_too is not None and affirms_too(part))

        if self.negates(said):
            return Cue.NEGATIVE
        if affirmed(said):
            return Cue.AFFIRMATIVE
        if not _real(said):
            return Cue.SILENT
        for split in range(1, len(said)):
            lead, rest = said[:split], said[split:]
            if affirmed(lead) and _real(rest) and not affirmed(rest):
                return Cue.CONTINUED
        return Cue.SPEECH

    def yields(self, said: Said, affirms_too: Affirms | None = None) -> bool:
        return self.cue(said, affirms_too) in YIELDING

    def _affirmative_at(self, said: Said, at: int) -> int:
        longest = 0
        for phrase in self.affirmatives:
            if len(phrase) > longest and said[at : at + len(phrase)] == phrase:
                longest = len(phrase)
        return longest


NO_CUES = Lexicon((), ())


__all__ = ["NO_CUES", "YIELDING", "Cue", "Lexicon", "phrases"]
