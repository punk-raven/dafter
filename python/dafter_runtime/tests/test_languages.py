from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from dafter_core.enums import TurnStrategy
from dafter_providers import VENDORS, sarvam
from dafter_runtime.personas import base_language
from dafter_runtime.plan import TURN_DETECTOR_LANGUAGES, load, turn_detection

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "go" / "cmd" / "dafter-control" / "catalog.json"
JOBS = ROOT / "testdata" / "agent"
FOCUS = {
    "hi": "hindi-webrtc-job.json",
    "en-IN": "english-webrtc-job.json",
    "kn-IN": "kannada-webrtc-job.json",
    "mr-IN": "marathi-webrtc-job.json",
    "te-IN": "telugu-webrtc-job.json",
}
SARVAM = VENDORS["sarvam"]


@pytest.fixture(autouse=True)
def sarvam_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARVAM_API_KEY", "test-only-not-a-key")


def catalog_languages() -> dict[str, Any]:
    languages: dict[str, Any] = json.loads(CATALOG.read_text(encoding="utf-8"))["languages"]
    return languages


def test_the_catalog_routes_every_focus_language() -> None:
    assert set(catalog_languages()) == set(FOCUS)


@pytest.mark.parametrize("language", sorted(FOCUS))
def test_semantic_is_chosen_only_where_the_on_device_detector_covers_the_language(
    language: str,
) -> None:
    turn = catalog_languages()[language]["tuning"]["turn"]
    if base_language(language) in TURN_DETECTOR_LANGUAGES:
        assert turn["strategy"] in {"semantic", "provider_endpointing"}
    else:
        assert turn["strategy"] == "provider_endpointing"
        assert turn["localVadEnabled"] is False


@pytest.mark.parametrize(("language", "fixture"), sorted(FOCUS.items()))
def test_each_job_vector_hears_and_speaks_its_own_language(language: str, fixture: str) -> None:
    cfg = load((JOBS / fixture).read_bytes().strip())
    assert cfg.language == language
    pipeline = cfg.agent.pipeline
    assert pipeline is not None and pipeline.stt and pipeline.llm and pipeline.tts
    for ref in (pipeline.stt, pipeline.llm, pipeline.tts):
        assert ref.provider == "sarvam"
        assert language in SARVAM.languages
    expected = "semantic" if cfg.turn.strategy is TurnStrategy.SEMANTIC else "stt"
    assert turn_detection(cfg.turn, SARVAM, language) == expected
    stt = sarvam.build_stt(pipeline.stt, language, cfg.turn, None)
    tts = sarvam.build_tts(pipeline.tts, language)
    code = sarvam.LANGUAGES[language]
    assert stt._opts.language == code  # type: ignore[attr-defined]
    assert str(tts._opts.target_language_code) == code  # type: ignore[attr-defined]
    assert tts._opts.speaker == pipeline.tts.options["voice"]  # type: ignore[attr-defined]


@pytest.mark.parametrize(("language", "fixture"), sorted(FOCUS.items()))
def test_every_indic_language_keeps_the_english_words_its_callers_mix_in(
    language: str, fixture: str
) -> None:
    cfg = load((JOBS / fixture).read_bytes().strip())
    assert cfg.agent.pipeline is not None and cfg.agent.pipeline.stt is not None
    stt = sarvam.build_stt(cfg.agent.pipeline.stt, language, cfg.turn, None)
    expected = "transcribe" if base_language(language) == "en" else "codemix"
    assert stt._opts.mode == expected  # type: ignore[attr-defined]
