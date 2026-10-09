from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError
from dafter_providers import TURN_DETECTORS, smart_turn, turn_detector_for
from dafter_providers.smart_turn import weights
from dafter_providers.smart_turn.detector import (
    COMPLETE_AT,
    COMPLETE_WHEN_UNSURE,
    SAMPLE_RATE,
    WINDOW_SAMPLES,
    Pcm,
    SmartTurnDetector,
    last_window,
)
from livekit import rtc
from livekit.agents.language import LanguageCode
from livekit.agents.voice.turn import _StreamingTurnDetector, _StreamingTurnDetectorStream

FAKE_WEIGHTS = b"not an onnx graph"


@pytest.fixture(autouse=True)
def cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv(weights.CACHE_ENV, str(tmp_path))
    return tmp_path


def writing(content: bytes, calls: list[str]) -> weights.Download:
    def download(url: str, into: Path) -> None:
        calls.append(url)
        into.write_bytes(content)

    return download


def test_the_weights_are_pinned_to_one_revision_and_file() -> None:
    assert weights.WEIGHTS_URL == (
        "https://huggingface.co/pipecat-ai/smart-turn-v3/resolve/"
        "f766f81d3cfdf7737ac64aad813d91bbfd56bf93/smart-turn-v3.2-cpu.onnx"
    )
    assert weights.weights_path().parts[-2:] == (weights.REVISION, weights.WEIGHTS_FILE)


def test_fetched_weights_land_only_when_their_checksum_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(weights, "WEIGHTS_SHA256", hashlib.sha256(FAKE_WEIGHTS).hexdigest())
    calls: list[str] = []
    path = weights.fetch(writing(FAKE_WEIGHTS, calls))
    assert path.read_bytes() == FAKE_WEIGHTS
    assert weights.fetch(writing(b"", calls)) == path
    assert calls == [weights.WEIGHTS_URL]
    assert list(path.parent.iterdir()) == [path]


def test_weights_with_another_checksum_are_discarded() -> None:
    with pytest.raises(weights.WeightsMismatchError):
        weights.fetch(writing(FAKE_WEIGHTS, []))
    assert not any(weights.weights_path().parent.iterdir())
    assert smart_turn.missing() is not None


def recorded_prewarm(
    monkeypatch: pytest.MonkeyPatch, cached: bool, fetched: Callable[[], Path]
) -> list[str]:
    calls: list[str] = []

    def fetch() -> Path:
        calls.append("fetch")
        return fetched()

    def load(path: Path) -> None:
        calls.append("load")

    monkeypatch.setattr(smart_turn, "fetch", fetch)
    monkeypatch.setattr(smart_turn, "_model", load)
    monkeypatch.setattr(smart_turn, "missing", lambda: None if cached else "not fetched")
    smart_turn.prewarm()
    return calls


def offline() -> Path:
    raise OSError("no route to host")


def test_prewarm_downloads_nothing_unless_the_worker_opts_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(smart_turn.PREWARM_ENV, raising=False)
    assert recorded_prewarm(monkeypatch, cached=False, fetched=offline) == []


def test_prewarm_loads_weights_already_cached_without_the_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(smart_turn.PREWARM_ENV, raising=False)
    assert recorded_prewarm(monkeypatch, cached=True, fetched=offline) == ["load"]


def test_an_opted_in_worker_fetches_and_loads_the_weights_at_prewarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(smart_turn.PREWARM_ENV, "1")
    calls = recorded_prewarm(monkeypatch, cached=True, fetched=weights.weights_path)
    assert calls == ["fetch", "load"]


def test_a_failed_fetch_at_prewarm_leaves_the_worker_running(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(smart_turn.PREWARM_ENV, "1")
    assert recorded_prewarm(monkeypatch, cached=False, fetched=offline) == ["fetch"]


def test_the_detector_is_refused_without_its_weights() -> None:
    with pytest.raises(DafterError) as caught:
        smart_turn.build_detector()
    assert caught.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert caught.value.details[0].startswith("at '/turn/detector'")


def test_the_window_keeps_the_last_eight_seconds_and_pads_silence_before_shorter_speech() -> None:
    short = np.ones(SAMPLE_RATE, dtype=np.float32)
    padded = last_window(short)
    assert len(padded) == WINDOW_SAMPLES
    assert not padded[: WINDOW_SAMPLES - SAMPLE_RATE].any()
    assert padded[-SAMPLE_RATE:].all()
    long = np.arange(WINDOW_SAMPLES + 5, dtype=np.float32)
    assert last_window(long)[0] == 5


def test_both_registered_detectors_cover_hindi_and_only_smart_turn_covers_marathi() -> None:
    livekit, smart = TURN_DETECTORS["livekit"], TURN_DETECTORS[smart_turn.NAME]
    assert {"en", "hi"} <= livekit.languages and "mr" not in livekit.languages
    assert {"en", "hi", "mr"} <= smart.languages
    assert not {"te", "kn"} & (livekit.languages | smart.languages)
    assert livekit.missing() is None


def test_an_unregistered_detector_is_refused_at_its_pointer() -> None:
    with pytest.raises(DafterError) as caught:
        turn_detector_for("multilingual")
    assert caught.value.details[0].startswith("at '/turn/detector'")


def frame(samples: int, value: int = 1000) -> rtc.AudioFrame:
    pcm = np.full(samples, value, dtype=np.int16)
    return rtc.AudioFrame(pcm.tobytes(), SAMPLE_RATE, 1, samples)


async def drained() -> None:
    await asyncio.sleep(0.05)


def test_the_stream_predicts_on_the_audio_since_the_last_flush() -> None:
    heard: list[int] = []

    def predict(pcm: Pcm) -> float:
        heard.append(len(pcm))
        return 0.8

    async def run() -> None:
        detector = SmartTurnDetector(predict, smart_turn.LANGUAGES)
        assert isinstance(detector, _StreamingTurnDetector)
        stream = detector.stream()
        assert isinstance(stream, _StreamingTurnDetectorStream)
        stream.push_audio(frame(1600))
        stream.push_audio(frame(1600))
        await drained()
        event = await stream.predict()
        assert event.end_of_turn_probability == 0.8
        stream.flush()
        stream.push_audio(frame(800))
        await drained()
        await stream.predict()
        await stream.aclose()
        assert (detector.model, detector.provider, stream.model) == (
            smart_turn.MODEL,
            "pipecat",
            smart_turn.MODEL,
        )

    asyncio.run(run())
    assert heard == [3200, 800]


def test_a_failed_prediction_lets_the_turn_end_on_the_short_delay() -> None:
    def predict(pcm: Pcm) -> float:
        raise RuntimeError("bad graph")

    async def run() -> float:
        stream = SmartTurnDetector(predict, smart_turn.LANGUAGES).stream()
        stream.push_audio(frame(1600))
        await drained()
        event = await stream.predict()
        await stream.aclose()
        return event.end_of_turn_probability

    assert asyncio.run(run()) == COMPLETE_WHEN_UNSURE


def test_the_detector_judges_complete_above_one_half_in_each_language_it_covers() -> None:
    async def run() -> None:
        detector = SmartTurnDetector(lambda pcm: 0.0, smart_turn.LANGUAGES)
        assert await detector.unlikely_threshold(LanguageCode("mr")) == COMPLETE_AT
        assert await detector.backchannel_threshold(LanguageCode("mr")) is None
        assert await detector.supports_language(LanguageCode("mr"))
        assert await detector.supports_language(None)
        assert not await detector.supports_language(LanguageCode("te"))
        stream = detector.stream()
        assert not await stream.supports_language(LanguageCode("kn"))
        await stream.aclose()

    asyncio.run(run())
