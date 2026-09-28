from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from dafter_core.enums import TurnStrategy
from dafter_core.hashing import seal
from dafter_providers import VENDORS, sarvam
from dafter_runtime.personas import base_language, persona_for
from dafter_runtime.plan import TURN_DETECTOR_LANGUAGES, load, plan, turn_detection

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


SCRIPTS = {
    "hi": (0x0900, 0x097F),
    "mr": (0x0900, 0x097F),
    "kn": (0x0C80, 0x0CFF),
    "te": (0x0C00, 0x0C7F),
}


def in_script(text: str, base: str) -> bool:
    if base == "en":
        return text.isascii()
    low, high = SCRIPTS[base]
    return all(low <= ord(c) <= high for c in text if c.isalpha())


@pytest.mark.parametrize(("language", "fixture"), sorted(FOCUS.items()))
def test_every_focus_language_plans_with_personas_in_its_own_script(
    language: str, fixture: str
) -> None:
    p = plan(load((JOBS / fixture).read_bytes().strip()), "dafter-py")
    assert (p.stt.name, p.llm.name, p.tts.name) == ("sarvam", "sarvam", "sarvam")
    base = base_language(language)
    for ref in (None, "persona://support/v3"):
        persona = persona_for(ref, language, p.config.agent.name, p.config.agent.addressing.aliases)
        assert in_script(persona.greeting, base), persona.greeting
        assert "Reply only in" in persona.instructions
        if base not in {"hi", "en"}:
            assert "never write digits" in persona.instructions


def test_a_session_may_switch_between_all_five_focus_languages() -> None:
    doc = json.loads((JOBS / FOCUS["kn-IN"]).read_bytes())
    doc["agent"]["languageSwitching"]["enabled"] = True
    sealed, _ = seal(json.dumps(doc))
    p = plan(load(sealed), "dafter-py")
    assert p.hearing is None
    assert set(p.personas) == set(FOCUS)
    assert p.personas["kn-IN"] == p.persona
