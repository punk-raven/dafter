from __future__ import annotations

import asyncio
import math
from collections.abc import AsyncIterator

import numpy as np
from dafter_runtime.loudness import GAIN_LIMIT_DB, SPEECH_DBFS, Loudness
from livekit import rtc

RATE = 24000
FRAME = 480


def tone(db: float, seconds: float) -> list[rtc.AudioFrame]:
    amplitude = 10 ** (db / 20) * math.sqrt(2) * 32767
    t = np.arange(int(seconds * RATE)) / RATE
    samples = (amplitude * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
    return [
        rtc.AudioFrame(
            data=samples[i : i + FRAME].tobytes(),
            sample_rate=RATE,
            num_channels=1,
            samples_per_channel=FRAME,
            userdata={"at": i},
        )
        for i in range(0, len(samples) - FRAME + 1, FRAME)
    ]


def level(frames: list[rtc.AudioFrame]) -> float:
    samples = np.concatenate([np.frombuffer(f.data, dtype=np.int16) for f in frames])
    return 10 * math.log10(float(np.mean(samples.astype(np.float64) ** 2)) / 32767**2)


def played(loudness: Loudness, frames: list[rtc.AudioFrame]) -> list[rtc.AudioFrame]:
    return [loudness.level(f) for f in frames]


def test_her_voice_at_a_typical_tts_level_is_brought_to_the_shared_speech_level() -> None:
    out = played(Loudness(), tone(-16, 10))
    assert abs(level(out[-100:]) - SPEECH_DBFS) < 0.5


def test_a_quieter_voice_is_lifted_but_never_by_more_than_the_limit() -> None:
    assert abs(level(played(Loudness(), tone(-30, 20))[-100:]) - SPEECH_DBFS) < 0.5
    lifted = level(played(Loudness(), tone(-45, 20))[-100:])
    assert abs(lifted - (-45 + GAIN_LIMIT_DB)) < 0.5


def test_pauses_between_sentences_keep_the_level_it_learned() -> None:
    loudness = Loudness()
    played(loudness, tone(-16, 10))
    learned = loudness.gain_db
    played(loudness, tone(-90, 5))
    assert abs(loudness.gain_db - learned) < 0.01


def test_a_leveled_frame_keeps_its_timing_and_its_metadata() -> None:
    frame = tone(-16, 0.02)[0]
    leveled = Loudness().level(frame)
    assert (leveled.sample_rate, leveled.samples_per_channel) == (RATE, FRAME)
    assert leveled.userdata is frame.userdata


def test_the_reply_stream_is_leveled_frame_by_frame() -> None:
    frames = tone(-16, 1)

    async def stream() -> AsyncIterator[rtc.AudioFrame]:
        for f in frames:
            yield f

    async def collect() -> list[rtc.AudioFrame]:
        return [f async for f in Loudness().leveled(stream())]

    out = asyncio.run(collect())
    assert len(out) == len(frames)
    assert level(out) < level(frames) - 6
