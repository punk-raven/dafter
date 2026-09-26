from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from typing import Any

import aiohttp
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import is_given
from livekit.plugins import sarvam as plugin
from livekit.plugins.sarvam.stt_streaming import RealtimeSTTOptions


class FinalFirstStream(plugin.RealtimeSpeechStream):
    def __init__(
        self,
        *,
        stt: plugin.STTRealtime,
        opts: RealtimeSTTOptions,
        conn_options: APIConnectOptions,
        http_session: aiohttp.ClientSession,
        final_grace: float,
    ) -> None:
        super().__init__(stt=stt, opts=opts, conn_options=conn_options, http_session=http_session)
        self._final_grace = final_grace

    def _handle_speech_end(self) -> None:
        if self._active_endpointing != "vad":
            super()._handle_speech_end()
            return
        self._utterance_speech_end_audio_pos = self._audio_position
        self._utterance_speech_end_wall = time.time()
        if self._final_received_for_utterance:
            self._try_commit_utterance()
        elif not self._eos_emitted_for_utterance:
            utterance = self._utterance_idx
            asyncio.get_running_loop().call_later(
                self._final_grace, self._release_end_of_speech, utterance
            )
        self._complete_utterance()

    def _release_end_of_speech(self, utterance: int | None) -> None:
        if self._utterance_idx == utterance and not self._eos_emitted_for_utterance:
            self._emit_end_of_speech()


class FinalFirstSTT(plugin.STTRealtime):
    def __init__(self, *, final_grace: float, **options: Any) -> None:
        super().__init__(**options)
        self._final_grace = final_grace

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> FinalFirstStream:
        opts: RealtimeSTTOptions = replace(
            self._opts, language=language if is_given(language) else self._opts.language
        )
        stream = FinalFirstStream(
            stt=self,
            opts=opts,
            conn_options=replace(conn_options, max_retry=0),
            http_session=self._ensure_session(),
            final_grace=self._final_grace,
        )
        self._streams.add(stream)
        return stream
