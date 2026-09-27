from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from dafter_core.config import Turn
from dafter_core.enums import ErrorCode, TurnStrategy
from dafter_core.errors import DafterError
from dafter_core.hashing import seal
from dafter_providers import VENDORS
from dafter_runtime.plan import TURN_DETECTOR_LANGUAGES, load, plan, turn_detection

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
SARVAM = VENDORS["sarvam"]
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
