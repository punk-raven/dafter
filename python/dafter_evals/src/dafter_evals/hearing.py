from __future__ import annotations

import asyncio
import io
import time
import wave
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import numpy as np
from livekit import rtc
from livekit.agents import stt

RATE = 16000
FRAME_S = 0.05
TAIL_S = 1.5
TIMEOUT_S = 30.0

Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class Heard:
    text: str
    languages: tuple[str, ...]
    confidences: tuple[float, ...]
    final_after_audio_ms: int | None


def pcm16(wav: bytes) -> np.ndarray:
    with wave.open(io.BytesIO(wav)) as w:
        if w.getsampwidth() != 2:
            raise ValueError("only 16-bit PCM clips are read")
        rate, channels = w.getframerate(), w.getnchannels()
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    mono = samples.reshape(-1, channels)[:, 0] if channels > 1 else samples
    if rate == RATE:
        return mono
    at = np.linspace(0, len(mono) - 1, num=round(len(mono) * RATE / rate))
    resampled: np.ndarray = np.interp(at, np.arange(len(mono)), mono).astype("<i2")
    return resampled


def frames(samples: np.ndarray) -> Iterator[rtc.AudioFrame]:
    step = int(RATE * FRAME_S)
    for start in range(0, len(samples), step):
        chunk = samples[start : start + step]
        yield rtc.AudioFrame(chunk.tobytes(), RATE, 1, len(chunk))


async def hear(
    recognizer: stt.STT[Any], wav: bytes, pace: float = 1.0, clock: Clock = time.monotonic
) -> Heard:
    samples = pcm16(wav)
    tail = np.zeros(int(RATE * TAIL_S), dtype="<i2")
    stream = recognizer.stream()
    texts: list[str] = []
    languages: list[str] = []
    confidences: list[float] = []
    last_final: list[float] = []

    async def listen() -> None:
        async for event in stream:
            if event.type is stt.SpeechEventType.FINAL_TRANSCRIPT and event.alternatives:
                said = event.alternatives[0]
                texts.append(said.text)
                languages.append(str(said.language))
                confidence = (said.metadata or {}).get("language_confidence")
                if isinstance(confidence, int | float) and not isinstance(confidence, bool):
                    confidences.append(float(confidence))
                last_final.append(clock())

    listening = asyncio.ensure_future(listen())
    try:
        for frame in frames(samples):
            stream.push_frame(frame)
            await asyncio.sleep(FRAME_S / pace)
        audio_ended = clock()
        for frame in frames(tail):
            stream.push_frame(frame)
            await asyncio.sleep(FRAME_S / pace)
        stream.end_input()
        await asyncio.wait_for(listening, TIMEOUT_S)
    finally:
        if not listening.done():
            listening.cancel()
        await stream.aclose()
    after = round((last_final[-1] - audio_ended) * 1000) if last_final else None
    return Heard(" ".join(texts).strip(), tuple(languages), tuple(confidences), after)


__all__ = ["Heard", "frames", "hear", "pcm16"]
