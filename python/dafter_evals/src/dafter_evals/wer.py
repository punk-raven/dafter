from __future__ import annotations

import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

NUKTAS = dict.fromkeys(map(ord, "\u093c\u0cbc\u0c3c"))
ANUSVARA_FOR_CHANDRABINDU = {0x0901: "\u0902", 0x0C81: "\u0c82", 0x0C01: "\u0c02"}
JOINERS = dict.fromkeys(map(ord, "\u200c\u200d\ufeff"))
INDIC_DIGITS = {zero + i: str(i) for zero in (0x0966, 0x0CE6, 0x0C66) for i in range(10)}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = text.translate(NUKTAS).translate(ANUSVARA_FOR_CHANDRABINDU)
    text = unicodedata.normalize("NFC", text).translate(JOINERS).translate(INDIC_DIGITS)
    text = "".join(" " if unicodedata.category(c)[0] in "PS" else c for c in text.casefold())
    return " ".join(text.split())


def words(text: str) -> list[str]:
    return normalise(text).split()


def characters(text: str) -> list[str]:
    return list("".join(normalise(text).split()))


Same = Callable[[int, int], bool]


@dataclass(frozen=True, slots=True)
class Edits:
    reference_length: int
    substitutions: int
    deletions: int
    insertions: int

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    @property
    def rate(self) -> float:
        return self.errors / self.reference_length


@dataclass(frozen=True, slots=True)
class Score:
    reference_words: int
    substitutions: int
    deletions: int
    insertions: int

    @property
    def errors(self) -> int:
        return self.substitutions + self.deletions + self.insertions

    @property
    def wer(self) -> float:
        return self.errors / self.reference_words

    def to_dict(self) -> dict[str, Any]:
        return {
            "wer": round(self.wer, 4),
            "referenceWords": self.reference_words,
            "substitutions": self.substitutions,
            "deletions": self.deletions,
            "insertions": self.insertions,
        }


def align(reference_length: int, hypothesis_length: int, same: Same) -> Edits:
    if reference_length == 0:
        raise ValueError("an error rate needs a reference with at least one unit")
    rows, cols = reference_length + 1, hypothesis_length + 1
    cost = [[(0, 0, 0)] * cols for _ in range(rows)]
    for i in range(1, rows):
        cost[i][0] = (0, i, 0)
    for j in range(1, cols):
        cost[0][j] = (0, 0, j)
    for i in range(1, rows):
        for j in range(1, cols):
            if same(i - 1, j - 1):
                cost[i][j] = cost[i - 1][j - 1]
                continue
            s, d, n = cost[i - 1][j - 1]
            candidates = [
                (s + 1, d, n),
                (cost[i - 1][j][0], cost[i - 1][j][1] + 1, cost[i - 1][j][2]),
                (cost[i][j - 1][0], cost[i][j - 1][1], cost[i][j - 1][2] + 1),
            ]
            cost[i][j] = min(candidates, key=lambda c: (sum(c), c))
    s, d, n = cost[-1][-1]
    return Edits(reference_length, s, d, n)


def edits(reference: Sequence[str], hypothesis: Sequence[str]) -> Edits:
    return align(len(reference), len(hypothesis), lambda i, j: reference[i] == hypothesis[j])


def score(reference: Sequence[str], hypothesis: Sequence[str]) -> Score:
    if not reference:
        raise ValueError("a word error rate needs a reference with at least one word")
    found = edits(reference, hypothesis)
    return Score(found.reference_length, found.substitutions, found.deletions, found.insertions)


def word_error_rate(reference: str, hypothesis: str) -> Score:
    return score(words(reference), words(hypothesis))


def character_error_rate(reference: str, hypothesis: str) -> Edits:
    return edits(characters(reference), characters(hypothesis))


def spelling_lattice(references: Sequence[str]) -> list[list[frozenset[str]]]:
    spellings = [spelling for spelling in map(words, references) if spelling]
    if not spellings:
        raise ValueError("an orthography-aware error rate needs a reference with a word")
    lattices: list[list[frozenset[str]]] = []
    for spelling in spellings:
        aligned = [s for s in spellings if len(s) == len(spelling)]
        lattice = [frozenset(s[i] for s in aligned) for i in range(len(spelling))]
        if lattice not in lattices:
            lattices.append(lattice)
    return lattices


def _heard_in(heard: Sequence[str], lattice: Sequence[frozenset[str]]) -> Same:
    return lambda i, j: heard[j] in lattice[i]


def orthography_aware_error_rate(references: Sequence[str], hypothesis: str) -> Edits:
    heard = words(hypothesis)
    scored = [
        align(len(lattice), len(heard), _heard_in(heard, lattice))
        for lattice in spelling_lattice(references)
    ]
    return min(scored, key=lambda e: (e.rate, e.errors))


@dataclass(frozen=True, slots=True)
class Metrics:
    wer: Score
    cer: Edits
    oiwer: Edits

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.wer.to_dict(),
            "cer": round(self.cer.rate, 4),
            "referenceCharacters": self.cer.reference_length,
            "characterErrors": self.cer.errors,
            "oiwer": round(self.oiwer.rate, 4),
            "oiwerReferenceWords": self.oiwer.reference_length,
            "oiwerErrors": self.oiwer.errors,
        }


def error_rates(references: Sequence[str], hypothesis: str) -> Metrics:
    if not references:
        raise ValueError("error rates need at least one reference")
    primary = references[0]
    return Metrics(
        word_error_rate(primary, hypothesis),
        character_error_rate(primary, hypothesis),
        orthography_aware_error_rate(references, hypothesis),
    )


def transcript_text(
    export: Mapping[str, Any], rendering: str = "verbatim", participant: str | None = None
) -> str:
    document = export.get("transcript", export)
    lines = document.get(rendering)
    if not isinstance(lines, list):
        raise ValueError(f"the transcript has no {rendering} rendering")
    return " ".join(
        str(line.get("text", ""))
        for line in lines
        if participant is None or line.get("speaker", {}).get("participantId") == participant
    )


__all__ = [
    "Edits",
    "Metrics",
    "Score",
    "align",
    "character_error_rate",
    "characters",
    "edits",
    "error_rates",
    "normalise",
    "orthography_aware_error_rate",
    "score",
    "spelling_lattice",
    "transcript_text",
    "word_error_rate",
    "words",
]
