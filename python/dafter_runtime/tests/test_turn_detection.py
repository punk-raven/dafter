from __future__ import annotations

import importlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from dafter_core.config import Turn
from dafter_core.enums import ErrorCode, TurnStrategy
from dafter_core.errors import DafterError
from dafter_core.hashing import seal
from dafter_providers import TURN_DETECTORS, VENDORS, TurnDetectorKind
from dafter_runtime.plan import TURN_DETECTOR_LANGUAGES, load, plan, turn_detection

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
SARVAM = VENDORS["sarvam"]
planning = importlib.import_module("dafter_runtime.plan")
SEMANTIC = Turn(strategy=TurnStrategy.SEMANTIC, local_vad_enabled=True)


def test_the_on_device_detector_covers_hindi_and_english_among_sarvam_languages() -> None:
    covered = {tag for tag in SARVAM.languages if tag.split("-")[0] in TURN_DETECTOR_LANGUAGES}
    assert covered == {"hi", "hi-IN", "en-IN"}


@pytest.mark.parametrize(
    ("language", "detection"),
    [
        ("hi", "semantic"),
        ("hi-IN", "semantic"),
        ("en-IN", "semantic"),
        ("ta-IN", "stt"),
        ("bn-IN", "stt"),
        ("or-IN", "stt"),
    ],
)
def test_semantic_runs_the_turn_detector_where_it_covers_the_language(
    language: str, detection: str
) -> None:
    assert turn_detection(SEMANTIC, SARVAM, language) == detection


def test_an_uncovered_language_without_provider_endpointing_is_refused() -> None:
    with pytest.raises(DafterError) as caught:
        turn_detection(SEMANTIC, VENDORS["silero"], "ta-IN")
    assert caught.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert "/turn/strategy" in caught.value.details[0]


def test_a_covered_language_refuses_the_detector_without_its_vad() -> None:
    with pytest.raises(DafterError) as caught:
        turn_detection(Turn(strategy=TurnStrategy.SEMANTIC, local_vad_enabled=False), SARVAM, "hi")
    assert "/turn/localVadEnabled" in caught.value.details[0]
    assert (
        turn_detection(
            Turn(strategy=TurnStrategy.SEMANTIC, local_vad_enabled=False), SARVAM, "ta-IN"
        )
        == "stt"
    )


def semantic_job(**turn: Any) -> bytes:
    doc = json.loads(JOB.read_bytes())
    doc["turn"].update(strategy="semantic", localVadEnabled=True, **turn)
    sealed, _ = seal(json.dumps(doc))
    return sealed


def test_a_semantic_hindi_job_plans_the_detector_in_its_dynamic_mode() -> None:
    p = plan(load(semantic_job(endpointingDelayMs=300)), "dafter-py")
    assert p.turn_detection == "semantic"
    assert p.vad is not None and p.vad.name == "silero"
    assert "turn_detection" not in p.turn_handling
    assert p.turn_handling["endpointing"] == {"mode": "dynamic", "min_delay": 0.3, "max_delay": 2.5}


def test_the_detector_needs_the_pipeline_vad_even_without_the_interruption_vad() -> None:
    doc = json.loads(semantic_job())
    doc["turn"]["interruption"]["localVadEnabled"] = False
    doc["agent"]["pipeline"].pop("vad")
    sealed, _ = seal(json.dumps(doc))
    with pytest.raises(DafterError) as caught:
        plan(load(sealed), "dafter-py")
    assert caught.value.code is ErrorCode.INVALID_CONFIG
    assert "/agent/pipeline/vad" in caught.value.details[0]


@pytest.mark.parametrize("fixture", ["hindi-semantic-webrtc-job.json", "english-webrtc-job.json"])
def test_the_catalog_resolves_jobs_that_run_the_turn_detector(fixture: str) -> None:
    p = plan(load(JOB.with_name(fixture).read_bytes().strip()), "dafter-py")
    assert p.turn_detection == "semantic"
    assert (p.stt.name, p.llm.name, p.tts.name) == ("sarvam", "sarvam", "sarvam")
    assert p.vad is not None and p.vad.name == "silero"
    assert p.persona.greeting


MARATHI = JOB.with_name("marathi-webrtc-job.json")


def smart_turn_job(language_job: Path = MARATHI, **turn: Any) -> bytes:
    doc = json.loads(language_job.read_bytes())
    doc["turn"].update(strategy="semantic", localVadEnabled=True, detector="smart_turn", **turn)
    sealed, _ = seal(json.dumps(doc))
    return sealed


@pytest.fixture
def smart_turn_ready(monkeypatch: pytest.MonkeyPatch) -> TurnDetectorKind:
    kind = replace(TURN_DETECTORS["smart_turn"], missing=lambda: None)
    chosen = {**TURN_DETECTORS, "smart_turn": kind}
    monkeypatch.setattr(planning, "turn_detector_for", chosen.__getitem__)
    return kind


def test_the_catalog_runs_marathi_on_the_recognizer_until_a_session_turns_on_the_trial() -> None:
    p = plan(load(MARATHI.read_bytes().strip()), "dafter-py")
    assert p.config.turn.detector == "livekit"
    assert (p.turn_detection, p.turn_detector) == ("stt", None)


def test_a_marathi_session_that_turns_on_the_trial_plans_smart_turn(
    smart_turn_ready: TurnDetectorKind,
) -> None:
    p = plan(load(smart_turn_job()), "dafter-py")
    assert p.turn_detection == "semantic"
    assert p.turn_detector is smart_turn_ready
    assert p.turn_handling["endpointing"]["mode"] == "dynamic"


def test_the_trial_is_refused_before_joining_on_a_worker_without_its_weights(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    kind = replace(TURN_DETECTORS["smart_turn"], missing=lambda: "weights not fetched")
    monkeypatch.setattr(planning, "turn_detector_for", {"smart_turn": kind}.__getitem__)
    with pytest.raises(DafterError) as caught:
        plan(load(smart_turn_job()), "dafter-py")
    assert caught.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert caught.value.details[0] == "at '/turn/detector': smart_turn: weights not fetched"


@pytest.mark.usefixtures("smart_turn_ready")
def test_the_trial_leaves_telugu_on_the_recognizer() -> None:
    p = plan(load(smart_turn_job(JOB.with_name("telugu-webrtc-job.json"))), "dafter-py")
    assert (p.turn_detection, p.turn_detector) == ("stt", None)


def test_the_default_detector_is_planned_wherever_the_semantic_strategy_runs() -> None:
    p = plan(load(semantic_job()), "dafter-py")
    assert p.turn_detector is TURN_DETECTORS["livekit"]
