from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions, tokenize
from livekit.agents.tokenize.tokenizer import SentenceStream
from livekit.agents.tts import AudioEmitter
from livekit.plugins import sarvam as plugin
from livekit.plugins.sarvam.tts import SynthesizeStream

from .voices import Voice

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


class FirstSentenceAlone(SynthesizeStream):
    def __init__(self, *, tts: plugin.TTS, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._head = ""
        self._rest = ""
        self._led = False
        self._rest_started = False
        self._lead_ended = False

    def push_text(self, token: str) -> None:
        if not token:
            return
        if self._led:
            self._follow(token)
            return
        self._head += token
        end = SENTENCE_END.search(self._head)
        if end is None:
            super().push_text(token)
            return
        cut = len(token) - (len(self._head) - end.end())
        super().push_text(token[:cut])
        self._led = True
        self._send_alone()
        self._follow(token[cut:])

    def _follow(self, token: str) -> None:
        self._rest += token
        if not self._rest.strip():
            return
        if not self._rest_started:
            self._rest_started = True
            self._num_segments += 1
        super().push_text(self._rest)
        self._rest = ""

    def _send_alone(self) -> None:
        if self._input_ch.closed:
            return
        sentinel = self._FlushSentinel()
        self._input_ch.send_nowait(sentinel)
        self._input_buffer.append(sentinel)

    async def _handle_event_message(
        self, resp: dict[str, Any], output_emitter: AudioEmitter
    ) -> bool:
        if self._lead_ended or resp.get("data", {}).get("event_type") != "final":
            return bool(await super()._handle_event_message(resp, output_emitter))
        self._lead_ended = True
        output_emitter.end_segment()
        return False


class SentenceTTS(plugin.TTS):
    def __init__(
        self,
        *,
        voice: Voice,
        styles: Mapping[str, Voice] | None = None,
        languages: Mapping[str, str] | None = None,
        first_sentence_alone: bool = False,
        **options: Any,
    ) -> None:
        super().__init__(pace=voice.pace, temperature=voice.temperature, **options)
        self._opts.word_tokenizer = SentenceTokenizer()
        self._voice = voice
        self._styles = dict(styles or {})
        self._languages = dict(languages or {})
        self._first_sentence_alone = first_sentence_alone

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> SynthesizeStream:
        if not self._first_sentence_alone:
            return super().stream(conn_options=conn_options)
        stream = FirstSentenceAlone(tts=self, conn_options=conn_options)
        self._streams.add(stream)
        return stream

    def speak_in(self, language: str) -> None:
        code = self._languages.get(language)
        if code is not None and code != self._opts.target_language_code:
            self.update_options(target_language_code=code)

    def style(self, situation: str) -> None:
        voice = self._styles.get(situation, self._voice)
        self.update_options(pace=voice.pace, temperature=voice.temperature)
