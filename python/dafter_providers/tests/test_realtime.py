from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
import pytest
from dafter_providers.sarvam import realtime
from dafter_providers.sarvam.realtime import FinalFirstStream, FinalFirstSTT
from livekit import rtc
from livekit.agents import stt

Kind = stt.SpeechEventType


class Recorder:
    closed = False

    def __init__(self) -> None:
        self.kinds: list[Kind] = []

    def send_nowait(self, event: stt.SpeechEvent) -> None:
        self.kinds.append(event.type)

    def close(self) -> None:
        return None


@contextlib.asynccontextmanager
async def stream() -> AsyncIterator[tuple[FinalFirstStream, Recorder]]:
    async with aiohttp.ClientSession() as http:
        model = FinalFirstSTT(
            language="hi-IN",
            stream_type="fast",
            api_key="test-only-not-a-key",
            base_url="ws://127.0.0.1:9/unreachable",
            http_session=http,
        )
        s = model.stream()
        recorder = Recorder()
        s._event_ch = recorder  # type: ignore[assignment]
        try:
            yield s, recorder
        finally:
            with contextlib.suppress(Exception):
                await s.aclose()


def final(text: str) -> dict[str, Any]:
    return {"event": "transcript.final", "text": text, "utterance_idx": 0}


def test_end_of_speech_is_held_until_the_final_transcript_arrives() -> None:
    async def run() -> list[Kind]:
        async with stream() as (s, recorder):
            await s._handle_message({"event": "vad.speech_start", "utterance_idx": 0})
            await s._handle_message({"event": "vad.speech_end", "utterance_idx": 0})
            assert recorder.kinds == [Kind.START_OF_SPEECH]
            await s._handle_message(final("नमस्ते"))
            return recorder.kinds

    assert asyncio.run(run()) == [Kind.START_OF_SPEECH, Kind.FINAL_TRANSCRIPT, Kind.END_OF_SPEECH]


def frame(level: int) -> rtc.AudioFrame:
    return rtc.AudioFrame(level.to_bytes(2, "little", signed=True) * 160, 16000, 1, 160)


def test_the_endpoint_runs_from_the_last_voiced_frame_to_the_held_end_of_speech() -> None:
    async def run() -> float:
        async with stream() as (s, _):
            model = s._stt
            assert isinstance(model, FinalFirstSTT)
            await s._handle_message({"event": "vad.speech_start", "utterance_idx": 0})
            s.push_frame(frame(8000))
            voiced_by = time.time()
            await asyncio.sleep(0.1)
            s.push_frame(frame(0))
            s.push_frame(frame(200))
            await s._handle_message({"event": "vad.speech_end", "utterance_idx": 0})
            assert model.take_endpoint() is None
            await asyncio.sleep(0.1)
            await s._handle_message(final("नमस्ते"))
            found = model.take_endpoint()
            assert found is not None and found.voiced_until <= voiced_by
            assert model.take_endpoint() is None
            return found.released_at - found.voiced_until

    assert asyncio.run(run()) >= 0.2


def test_a_final_that_beat_the_speech_end_is_released_by_it() -> None:
    async def run() -> list[Kind]:
        async with stream() as (s, recorder):
            await s._handle_message({"event": "vad.speech_start", "utterance_idx": 0})
            await s._handle_message(final("नमस्ते"))
            await s._handle_message({"event": "vad.speech_end", "utterance_idx": 0})
            return recorder.kinds

    assert asyncio.run(run()) == [Kind.START_OF_SPEECH, Kind.FINAL_TRANSCRIPT, Kind.END_OF_SPEECH]


def test_a_final_that_never_comes_still_ends_the_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(realtime, "FINAL_GRACE_S", 0.05)

    async def run() -> list[Kind]:
        async with stream() as (s, recorder):
            await s._handle_message({"event": "vad.speech_start", "utterance_idx": 0})
            await s._handle_message({"event": "vad.speech_end", "utterance_idx": 0})
            await asyncio.sleep(0.2)
            return recorder.kinds

    assert asyncio.run(run()) == [Kind.START_OF_SPEECH, Kind.END_OF_SPEECH]
