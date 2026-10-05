from __future__ import annotations

import re
import unicodedata
from collections.abc import AsyncIterable, AsyncIterator, Callable, Mapping, Sequence

from dafter_core.enums import SpeechNormalization
from dafter_core.speech import Speech
from livekit.agents.voice.transcription.text_transforms import TextTransforms

from . import spoken_hindi
from .personas import base_language

Normalizer = Callable[[str], str]

NORMALIZERS: Mapping[str, Normalizer] = {"hi": spoken_hindi.normalize}
FRAMEWORK_TRANSFORMS: tuple[TextTransforms, ...] = ("filter_markdown", "filter_emoji")

SENTENCE_END = re.compile(r"[.!?\u0964\u0965][\"'\u201d\u2019)\]]*\s+(?![\d\u20b9])|\n")
LIST_MARKER = re.compile(r"^[ \t]*\d{1,2}[.)][ \t]+", re.MULTILINE)
MAX_HELD = 300


def is_word_character(c: str) -> bool:
    return unicodedata.category(c)[0] in "LMN"


class Substitutions:
    def __init__(self, words: Mapping[str, str]) -> None:
        self._words = {unicodedata.normalize("NFC", k): v for k, v in words.items()}
        keys = sorted(self._words, key=len, reverse=True)
        self._pattern = re.compile("|".join(re.escape(k) for k in keys)) if keys else None

    def __call__(self, text: str) -> str:
        if self._pattern is None:
            return text
        text = unicodedata.normalize("NFC", text)

        def swap(match: re.Match[str]) -> str:
            start, end = match.span()
            before = text[start - 1] if start else " "
            after = text[end] if end < len(text) else " "
            if is_word_character(before) or is_word_character(after):
                return match.group(0)
            return self._words[match.group(0)]

        return self._pattern.sub(swap, text)


class SpeechPlan:
    def __init__(self, speech: Speech, language: str) -> None:
        base = base_language(language)
        self._substitute = Substitutions(speech.substitutions.get(base, {}))
        self._normalize = (
            NORMALIZERS.get(base) if speech.normalization is SpeechNormalization.PLATFORM else None
        )

    def spoken(self, text: str) -> str:
        text = self._substitute(LIST_MARKER.sub("", text))
        return self._normalize(text) if self._normalize is not None else text

    async def transform(self, text: AsyncIterable[str]) -> AsyncIterator[str]:
        held = ""
        async for chunk in text:
            held += chunk
            cut = sentence_cut(held)
            if cut:
                yield self.spoken(held[:cut])
                held = held[cut:]
        if held:
            yield self.spoken(held)

    def transforms(self) -> Sequence[TextTransforms]:
        return [*FRAMEWORK_TRANSFORMS, self.transform]


def sentence_cut(text: str) -> int:
    ends = [m.end() for m in SENTENCE_END.finditer(text)]
    if ends:
        return ends[-1]
    if len(text) <= MAX_HELD:
        return 0
    space = text.rfind(" ")
    while space > 0 and (text[space - 1].isdigit() or text[space + 1 : space + 2].isdigit()):
        space = text.rfind(" ", 0, space)
    return space + 1 if space > 0 else 0


__all__ = ["NORMALIZERS", "SpeechPlan", "Substitutions", "sentence_cut"]
