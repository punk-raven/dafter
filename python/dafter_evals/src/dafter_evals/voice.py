from __future__ import annotations

from dataclasses import replace

import numpy as np
from dafter_core.config import ResolvedSessionConfig
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError
from dafter_providers import vendor_for

from .probe import SAMPLE_RATE, trim


class Voice:
    def __init__(self, cfg: ResolvedSessionConfig, speaker: str) -> None:
        pipeline = cfg.agent.pipeline
        ref = pipeline.tts if pipeline else None
        vendor = vendor_for(ref, Stage.TTS)
        if ref is None or vendor.tts is None:
            raise DafterError(ErrorCode.INVALID_CONFIG, "the session names no tts to speak with")
        options = {**ref.options, "voice": speaker, "sampleRate": SAMPLE_RATE, "prewarm": False}
        self._tts = vendor.tts(replace(ref, options=options), cfg.language)
        self._cache: dict[str, np.ndarray] = {}

    async def say(self, text: str) -> np.ndarray:
        if text not in self._cache:
            chunks: list[np.ndarray] = []
            async with self._tts.synthesize(text) as stream:
                async for audio in stream:
                    frame = audio.frame
                    if frame.sample_rate != SAMPLE_RATE or frame.num_channels != 1:
                        raise DafterError(ErrorCode.INTERNAL, "synthesized audio in another shape")
                    chunks.append(np.frombuffer(frame.data, dtype=np.int16).copy())
            pcm = trim(np.concatenate(chunks)) if chunks else np.zeros(0, dtype=np.int16)
            if pcm.size == 0:
                raise DafterError(ErrorCode.PROVIDER_UNAVAILABLE, "the voice produced silence")
            self._cache[text] = pcm
        return self._cache[text]

    async def aclose(self) -> None:
        await self._tts.aclose()
