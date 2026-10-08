from __future__ import annotations

import logging
import math
from collections.abc import AsyncIterable, AsyncIterator

import numpy as np
from livekit import rtc

log = logging.getLogger("dafter.runtime.loudness")

SPEECH_DBFS = -25.0
TYPICAL_TTS_DBFS = -20.0
VOICED_DBFS = -50.0
SETTLE_S = 3.0
GAIN_LIMIT_DB = 12.0
FULL_SCALE = 32767
BLOCKS_PER_S = 100


def db(power: float) -> float:
    return 10 * math.log10(power) if power > 0 else -math.inf


class Loudness:
    def __init__(self, target: float = SPEECH_DBFS, heard: float = TYPICAL_TTS_DBFS) -> None:
        self.target = target
        self._power = 10 ** (heard / 10)

    @property
    def gain_db(self) -> float:
        return max(-GAIN_LIMIT_DB, min(GAIN_LIMIT_DB, self.target - db(self._power)))

    def level(self, frame: rtc.AudioFrame) -> rtc.AudioFrame:
        samples = np.frombuffer(frame.data, dtype=np.int16).astype(np.float64)
        if samples.size == 0:
            return frame
        block = max(1, frame.sample_rate * frame.num_channels // BLOCKS_PER_S)
        for start in range(0, samples.size, block):
            chunk = samples[start : start + block]
            power = float(np.mean(chunk**2)) / FULL_SCALE**2
            if db(power) > VOICED_DBFS:
                seconds = chunk.size / frame.num_channels / frame.sample_rate
                self._power += (power - self._power) * min(1.0, seconds / SETTLE_S)
        scaled = np.clip(samples * 10 ** (self.gain_db / 20), -FULL_SCALE, FULL_SCALE)
        return rtc.AudioFrame(
            data=scaled.astype(np.int16).tobytes(),
            sample_rate=frame.sample_rate,
            num_channels=frame.num_channels,
            samples_per_channel=frame.samples_per_channel,
            userdata=frame.userdata,
        )

    async def leveled(self, frames: AsyncIterable[rtc.AudioFrame]) -> AsyncIterator[rtc.AudioFrame]:
        async for frame in frames:
            yield self.level(frame)
        log.info(
            "reply leveled",
            extra={"heard_dbfs": round(db(self._power), 1), "gain_db": round(self.gain_db, 1)},
        )


__all__ = ["Loudness"]
