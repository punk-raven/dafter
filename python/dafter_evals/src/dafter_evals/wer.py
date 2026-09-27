from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

NUKTA = "\u093c"
CHANDRABINDU = "\u0901"
ANUSVARA = "\u0902"
JOINERS = dict.fromkeys(map(ord, "\u200c\u200d\ufeff"))
DEVANAGARI_DIGITS = {0x0966 + i: str(i) for i in range(10)}


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFD", text)
    text = text.replace(NUKTA, "").replace(CHANDRABINDU, ANUSVARA)
    text = unicodedata.normalize("NFC", text).translate(JOINERS).translate(DEVANAGARI_DIGITS)
    text = "".join(" " if unicodedata.category(c)[0] in "PS" else c for c in text.casefold())
    return " ".join(text.split())


def words(text: str) -> list[str]:
    return normalise(text).split()


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


def score(reference: Sequence[str], hypothesis: Sequence[str]) -> Score:
    if not reference:
        raise ValueError("a word error rate needs a reference with at least one word")
    rows, cols = len(reference) + 1, len(hypothesis) + 1
    cost = [[(0, 0, 0)] * cols for _ in range(rows)]
    for i in range(1, rows):
        cost[i][0] = (0, i, 0)
    for j in range(1, cols):
        cost[0][j] = (0, 0, j)
    for i in range(1, rows):
        for j in range(1, cols):
            if reference[i - 1] == hypothesis[j - 1]:
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
    return Score(len(reference), s, d, n)


def word_error_rate(reference: str, hypothesis: str) -> Score:
    return score(words(reference), words(hypothesis))


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


__all__ = ["Score", "normalise", "score", "transcript_text", "word_error_rate", "words"]
