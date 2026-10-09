from __future__ import annotations

import asyncio
import io
import json
import wave
from pathlib import Path
from typing import Any

import pytest
from dafter_core.config import parse
from dafter_evals.accuracy import IDENTIFY, estimate, latin_words, measure, setup
from dafter_evals.asr import audio_path, main, store
from dafter_evals.corpus import Clip, Sample
from dafter_evals.hearing import Heard, hear, pcm16
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectOptions,
    LanguageCode,
    stt,
    utils,
)
from livekit.agents.types import NOT_GIVEN, NotGivenOr

ROOT = Path(__file__).resolve().parents[3]
JOB = ROOT / "testdata" / "agent" / "kannada-webrtc-job.json"
MANIFEST = ROOT / "testdata" / "asr" / "kathbath-kn-IN.json"
SAMPLE_CLIPS = 20


def wav(seconds: float, rate: int = 16000, channels: int = 1) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\1\0" * int(rate * seconds) * channels)
    return out.getvalue()


class EchoStream(stt.RecognizeStream):
    def __init__(self, owner: Echo, conn_options: APIConnectOptions) -> None:
        super().__init__(stt=owner, conn_options=conn_options)
        self._owner = owner

    async def _run(self) -> None:
        async for _ in self._input_ch:
            pass
        for text, language, confidence in self._owner.finals:
            data = stt.SpeechData(
                language=LanguageCode(language),
                text=text,
                metadata=None if confidence is None else {"language_confidence": confidence},
            )
            self._event_ch.send_nowait(
                stt.SpeechEvent(type=stt.SpeechEventType.FINAL_TRANSCRIPT, alternatives=[data])
            )


class Echo(stt.STT[Any]):
    def __init__(self, finals: list[tuple[str, str, float | None]]) -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=True, interim_results=False))
        self.finals = finals

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
    ) -> EchoStream:
        return EchoStream(self, conn_options)


def test_a_clip_is_heard_through_the_stream_as_its_finals_and_languages() -> None:
    echo = Echo([("ನಮಸ್ಕಾರ", "kn-IN", 0.97), ("hello", "en-IN", None), ("ಸರಿ", "kn-IN", 0.6)])
    heard = asyncio.run(hear(echo, wav(0.2), pace=50))
    assert heard.text == "ನಮಸ್ಕಾರ hello ಸರಿ"
    assert heard.languages == ("kn-IN", "en-IN", "kn-IN")
    assert heard.confidences == (0.97, None, 0.6)
    assert heard.final_after_audio_ms is not None and heard.final_after_audio_ms >= 0


def test_clips_are_read_as_16_khz_mono() -> None:
    assert len(pcm16(wav(1.0))) == 16000
    assert len(pcm16(wav(1.0, rate=8000))) == 16000
    assert len(pcm16(wav(1.0, channels=2))) == 16000


def sample() -> Sample:
    return Sample.read(MANIFEST.read_text(encoding="utf-8"))


def test_a_run_scores_every_clip_and_reports_the_language_it_identified() -> None:
    s = sample()
    first, second = s.clips[:2]
    heard = {
        first.id: Heard(first.reference, ("kn-IN",), (0.9,), 400),
        second.id: Heard(second.reference.split(" ", 1)[1], ("hi-IN",), (0.5,), 600),
    }

    async def fake(clip: Clip) -> Heard:
        if clip.id not in heard:
            raise ConnectionError("gone")
        return heard[clip.id]

    trimmed = Sample(s.dataset, s.language, s.source, s.selection, s.clips[:3])
    setting = setup(parse(JOB.read_bytes()), trimmed, "transcribe", identify=True)
    report = asyncio.run(measure(trimmed, setting, fake, concurrency=2))
    summary = report["summary"]
    assert report["stt"] == {
        "provider": "sarvam",
        "model": "saaras:v3-realtime",
        "mode": "transcribe",
        "hearing": IDENTIFY,
    }
    assert (summary["clips"], summary["failed"]) == (2, 1)
    assert summary["deletions"] == 1 and summary["substitutions"] == 0
    words = len(first.reference.split()) + len(second.reference.split())
    assert summary["referenceWords"] == words
    assert summary["wer"] == round(1 / words, 4)
    assert (summary["identifiedCorrectly"], summary["identifiedOf"]) == (1, 2)
    assert summary["finalAfterAudioMs"] == {"p50": 500, "p95": 590}
    assert summary["costInr"] == float(estimate(setting, trimmed) or 0) > 0
    assert report["failures"] == [{"id": s.clips[2].id, "error": "ConnectionError"}]
    assert [c["languageConfidences"] for c in report["clips"]] == [[0.9], [0.5]]


def test_latin_script_words_are_counted_so_a_codemix_run_can_be_read_fairly() -> None:
    assert latin_words("ನಂತರ ಆಕೆ makeup ಇಲ್ಲದೆ real photo-ವನ್ನು 2024") == 3


def test_a_pinned_run_names_its_language_and_the_catalog_mode() -> None:
    setting = setup(parse(JOB.read_bytes()), sample(), None, identify=False)
    assert setting.to_dict()["hearing"] == "kn-IN"
    assert setting.to_dict()["mode"] == "codemix"


def test_a_run_over_its_budget_or_without_its_audio_never_starts(tmp_path: Path) -> None:
    (tmp_path / "sources.json").write_text("{}", encoding="utf-8")
    common = ["run", str(MANIFEST), "--job", str(JOB), "--testdata", str(tmp_path)]
    with pytest.raises(SystemExit, match="max-inr"):
        main([*common, "--max-inr", "0.01"])
    with pytest.raises(SystemExit, match="not fetched"):
        main(common)


def pinned_value(source: dict[str, Any], language: str, key: str) -> Any:
    if key in source:
        return source[key]
    return source["languages"][language][key]


def sample_size(source: dict[str, Any], language: str) -> int:
    per_language = source["languages"][language]
    if isinstance(per_language, dict):
        return min(SAMPLE_CLIPS, int(per_language.get("speakers", SAMPLE_CLIPS)))
    return SAMPLE_CLIPS


def test_the_committed_samples_are_pinned_to_their_sources() -> None:
    sources = json.loads((MANIFEST.parent / "sources.json").read_text(encoding="utf-8"))
    for manifest in sorted(MANIFEST.parent.glob("*-*.json")):
        s = Sample.read(manifest.read_text(encoding="utf-8"))
        pinned = sources[s.dataset]
        assert s.language in pinned["languages"]
        assert all(s.source[k] == pinned_value(pinned, s.language, k) for k in s.source)
        size = sample_size(pinned, s.language)
        assert len(s.clips) == size and len({c.speaker for c in s.clips}) == size
        assert all(len(c.sha256) == 64 and c.reference for c in s.clips)


def test_fetching_writes_only_the_missing_clips_after_checking_them(tmp_path: Path) -> None:
    s = sample()
    first, second = s.clips[:2]
    audio_path(tmp_path, s, first).parent.mkdir(parents=True)
    audio_path(tmp_path, s, first).write_bytes(b"already here")
    with pytest.raises(ValueError, match="sha256"):
        store(tmp_path, s, {second.id: b"changed"})
    assert audio_path(tmp_path, s, first).read_bytes() == b"already here"
    assert not audio_path(tmp_path, s, second).exists()
