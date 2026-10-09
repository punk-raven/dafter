from __future__ import annotations

import asyncio
import time
from typing import Any

from dafter_runtime.plan import Plan
from livekit import rtc
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectOptions,
    LanguageCode,
    stt,
    tts,
    utils,
    vad,
)
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.voice import io
from livekit.agents.voice.turn import TurnDetectionEvent

RATE = 16000
FRAME_S = 0.01
HINDI = LanguageCode("hi")


def silence(seconds: float, rate: int = RATE) -> rtc.AudioFrame:
    samples = int(rate * seconds)
    return rtc.AudioFrame(b"\0\0" * samples, rate, 1, samples)


class Microphone(io.AudioInput):
    def __init__(self) -> None:
        super().__init__(label="microphone")

    async def __anext__(self) -> rtc.AudioFrame:
        await asyncio.sleep(FRAME_S)
        return silence(FRAME_S)


class Speaker(io.AudioOutput):
    def __init__(self) -> None:
        super().__init__(label="speaker", capabilities=io.AudioOutputCapabilities(pause=True))
        self.paused = False
        self.pauses = 0
        self.played = 0.0
        self._queued = 0.0
        self._playing: asyncio.Task[None] | None = None

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        await super().capture_frame(frame)
        if self._queued == 0.0:
            self.on_playback_started(created_at=time.time())
        self._queued += frame.duration

    def flush(self) -> None:
        super().flush()
        self._playing = asyncio.ensure_future(self._play(self._queued))

    async def _play(self, duration: float) -> None:
        position = 0.0
        while position < duration:
            await asyncio.sleep(FRAME_S)
            if not self.paused:
                position += FRAME_S
                self.played += FRAME_S
        self._queued = 0.0
        self.on_playback_finished(playback_position=duration, interrupted=False)

    def clear_buffer(self) -> None:
        if self._playing is not None and not self._playing.done():
            self._playing.cancel()
            self.on_playback_finished(playback_position=0.0, interrupted=True)
        self._queued = 0.0

    def pause(self) -> None:
        self.paused = True
        self.pauses += 1

    def resume(self) -> None:
        self.paused = False


class ScriptedStream(stt.RecognizeStream):
    def __init__(self, owner: ScriptedSTT, conn_options: APIConnectOptions) -> None:
        super().__init__(stt=owner, conn_options=conn_options)
        self._script = owner.script

    async def _run(self) -> None:
        async def drain() -> None:
            async for _ in self._input_ch:
                pass

        draining = asyncio.ensure_future(drain())
        try:
            while True:
                self._event_ch.send_nowait(await self._script.get())
        finally:
            await utils.aio.cancel_and_wait(draining)


class ScriptedSTT(stt.STT[Any]):
    def __init__(self) -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=True, interim_results=True))
        self.script: asyncio.Queue[stt.SpeechEvent] = asyncio.Queue()

    def says(
        self, *texts: str, language: LanguageCode = HINDI, confidence: float | None = None
    ) -> None:
        self.script.put_nowait(stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH))
        for text in texts[:-1]:
            self._text(stt.SpeechEventType.INTERIM_TRANSCRIPT, text, language, None)
        self._text(stt.SpeechEventType.FINAL_TRANSCRIPT, texts[-1], language, confidence)
        self.script.put_nowait(stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH))

    def _text(
        self,
        kind: stt.SpeechEventType,
        text: str,
        language: LanguageCode,
        confidence: float | None,
    ) -> None:
        metadata = None if confidence is None else {"language_confidence": confidence}
        data = stt.SpeechData(language=language, text=text, metadata=metadata)
        self.script.put_nowait(stt.SpeechEvent(type=kind, alternatives=[data]))

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions,
    ) -> stt.SpeechEvent:
        raise NotImplementedError

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> ScriptedStream:
        return ScriptedStream(self, conn_options)


class ScriptedVADStream(vad.VADStream):
    def __init__(self, owner: ScriptedVAD) -> None:
        super().__init__(owner)
        self._script = owner.script

    async def _main_task(self) -> None:
        while True:
            self._event_ch.send_nowait(await self._script.get())


class ScriptedVAD(vad.VAD):
    def __init__(self) -> None:
        super().__init__(capabilities=vad.VADCapabilities(update_interval=FRAME_S))
        self.script: asyncio.Queue[vad.VADEvent] = asyncio.Queue()

    def talks(self, seconds: float) -> None:
        self._event(vad.VADEventType.START_OF_SPEECH, speaking=True)
        self._event(vad.VADEventType.INFERENCE_DONE, speaking=True, speech=seconds)

    def stops(self) -> None:
        self._event(vad.VADEventType.INFERENCE_DONE, speaking=False, silence=0.3)
        self._event(vad.VADEventType.END_OF_SPEECH, speaking=False, silence=0.3)

    def _event(
        self, kind: vad.VADEventType, speaking: bool, speech: float = 0.0, silence: float = 0.0
    ) -> None:
        self.script.put_nowait(
            vad.VADEvent(
                type=kind,
                samples_index=0,
                timestamp=time.time(),
                speech_duration=speech,
                silence_duration=silence,
                speaking=speaking,
                raw_accumulated_speech=speech,
                raw_accumulated_silence=silence,
            )
        )

    def stream(self) -> ScriptedVADStream:
        return ScriptedVADStream(self)


class Reading(tts.ChunkedStream):
    def __init__(self, owner: SlowReader, text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=owner, input_text=text, conn_options=conn_options)
        self._seconds = owner.seconds

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id="read", sample_rate=RATE, num_channels=1, mime_type="audio/pcm"
        )
        output_emitter.push(silence(self._seconds).data.tobytes())


class SlowReader(tts.TTS[Any]):
    def __init__(self, seconds: float) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False), sample_rate=RATE, num_channels=1
        )
        self.seconds = seconds
        self.read: list[str] = []

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> Reading:
        self.read.append(text)
        return Reading(self, text, conn_options)


class SureTurnStream:
    model = "sure"
    provider = "scripted"
    is_fallback = False
    prediction_timeout = 1.0

    async def unlikely_threshold(self, language: LanguageCode | None) -> float | None:
        return None

    async def backchannel_threshold(self, language: LanguageCode | None) -> float | None:
        return None

    async def supports_language(self, language: LanguageCode | None) -> bool:
        return True

    def predict(self) -> asyncio.Future[TurnDetectionEvent]:
        ended: asyncio.Future[TurnDetectionEvent] = asyncio.get_running_loop().create_future()
        ended.set_result(
            TurnDetectionEvent(
                type="eot_prediction", end_of_turn_probability=1.0, last_speaking_time=time.time()
            )
        )
        return ended

    def cancel_inference(self, *, timed_out: bool = False) -> None:
        pass

    def flush(self, reason: str | None = None) -> None:
        pass

    def push_audio(self, frame: rtc.AudioFrame) -> None:
        pass

    def end_input(self) -> None:
        pass

    async def aclose(self) -> None:
        pass


class SureTurnDetector:
    model = "sure"
    provider = "scripted"

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> SureTurnStream:
        return SureTurnStream()


def worker_turn_handling(p: Plan) -> dict[str, Any]:
    if p.turn_detector is None:
        return p.turn_handling
    return {**p.turn_handling, "turn_detection": SureTurnDetector()}


async def until(check: Any, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise AssertionError("the session never got there")
        await asyncio.sleep(FRAME_S)
