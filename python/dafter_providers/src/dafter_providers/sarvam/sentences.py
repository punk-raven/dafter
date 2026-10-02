from __future__ import annotations

import re
from typing import Any

from livekit.agents import tokenize
from livekit.agents.tokenize.tokenizer import SentenceStream
from livekit.plugins import sarvam as plugin

SENTENCE_END = re.compile(r"[.!?\u0964\u0965]+[\"'\u201d\u2019)\]]*\s+")
MIN_SENTENCE_LEN = 20
MIN_CONTEXT_LEN = 10


def split_sentences(text: str) -> list[tuple[str, int, int]]:
    sentences: list[tuple[str, int, int]] = []
    start = 0
    for match in SENTENCE_END.finditer(text):
        sentence = text[start : match.end()].strip()
        if sentence:
            sentences.append((sentence, start, match.end()))
        start = match.end()
    rest = text[start:].strip()
    if rest:
        sentences.append((rest, start, len(text)))
    return sentences


class SentenceTokenizer(tokenize.SentenceTokenizer):
    def tokenize(self, text: str, *, language: str | None = None) -> list[str]:
        return [sentence for sentence, _, _ in split_sentences(text)]

    def stream(self, *, language: str | None = None) -> SentenceStream:
        return tokenize.BufferedSentenceStream(
            tokenizer=split_sentences,
            min_token_len=MIN_SENTENCE_LEN,
            min_ctx_len=MIN_CONTEXT_LEN,
        )


class SentenceTTS(plugin.TTS):
    def __init__(self, **options: Any) -> None:
        super().__init__(**options)
        self._opts.word_tokenizer = SentenceTokenizer()
