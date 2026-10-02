from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError
from dafter_core.hashing import seal
from dafter_runtime.plan import load, plan

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
POOL = "dafter-py"


def job() -> bytes:
    return JOB.read_bytes().strip()


def variant(change: Callable[[dict[str, Any]], None]) -> bytes:
    doc = json.loads(job())
    change(doc)
    sealed, _ = seal(json.dumps(doc))
    return sealed


def refused(raw: bytes, pool: str = POOL) -> DafterError:
    with pytest.raises(DafterError) as caught:
        plan(load(raw), pool)
    return caught.value


def test_the_pinned_hindi_job_plans_the_sarvam_cascade() -> None:
    p = plan(load(job()), POOL)
    assert (p.stt.name, p.llm.name, p.tts.name) == ("sarvam", "sarvam", "sarvam")
    assert p.turn_detection == "stt"
    assert p.turn_handling == {
        "turn_detection": "stt",
        "endpointing": {"min_delay": 0.0},
        "interruption": {
            "enabled": True,
            "min_duration": 0.3,
            "min_words": 2,
            "false_interruption_timeout": 2.0,
            "resume_false_interruption": True,
        },
    }
    assert p.persona.greeting


def test_a_document_that_does_not_match_its_hash_is_refused() -> None:
    doc = json.loads(job())
    doc["turn"]["silenceMs"] = 900
    with pytest.raises(DafterError) as caught:
        load(json.dumps(doc))
    assert caught.value.code is ErrorCode.INVALID_CONFIG
    assert "/configHash" in caught.value.details[0]


def test_a_job_for_another_pool_is_refused() -> None:
    assert "/agent/pool" in refused(job(), pool="dafter-py-2").details[0]


@pytest.mark.parametrize(
    ("change", "code", "pointer"),
    [
        (
            lambda d: d["agent"].update(enabled=False),
            ErrorCode.INVALID_CONFIG,
            "/agent/enabled",
        ),
        (
            lambda d: d["agent"].update(mode="speech_to_speech"),
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "/agent/mode",
        ),
        (
            lambda d: d["agent"]["pipeline"]["stt"].update(provider="deepgram"),
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "/agent/pipeline/stt/provider",
        ),
        (
            lambda d: d.update(language="fr-FR"),
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "/language",
        ),
        (
            lambda d: d["turn"].update(strategy="semantic"),
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "/turn/strategy",
        ),
        (
            lambda d: d["turn"].update(localVadEnabled=True),
            ErrorCode.INVALID_CONFIG,
            "/turn/localVadEnabled",
        ),
        (
            lambda d: d["agent"].update(personaRef="persona://unknown/v1"),
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "/agent/personaRef",
        ),
    ],
)
def test_a_job_the_worker_cannot_run_is_refused_before_it_joins(
    change: Callable[[dict[str, Any]], None], code: ErrorCode, pointer: str
) -> None:
    err = refused(variant(change))
    assert err.code is code
    assert any(pointer in d for d in err.details), err.details


def trusted(d: dict[str, Any]) -> None:
    d["privacyMode"] = "trusted_agent"
    d["media"]["encryption"] = {"mode": "e2ee", "keyModel": "server_shared"}


def test_an_encrypted_room_is_refused_by_a_worker_that_cannot_fetch_its_key() -> None:
    err = refused(variant(trusted))
    assert err.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert "/media/encryption/mode" in err.details[0]
    assert "DAFTER_WORKER_SECRET" in err.details[0]


def test_a_worker_that_fetches_keys_plans_a_trusted_agent_room() -> None:
    p = plan(load(variant(trusted)), POOL, fetches_keys=True)
    assert p.config.media.encryption.mints_shared_key


def test_an_encrypted_room_without_the_shared_key_model_is_refused() -> None:
    def unkeyed(d: dict[str, Any]) -> None:
        trusted(d)
        del d["media"]["encryption"]["keyModel"]

    with pytest.raises(DafterError) as caught:
        plan(load(variant(unkeyed)), POOL, fetches_keys=True)
    assert caught.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert "/media/encryption/keyModel" in caught.value.details[0]


def test_auto_resolves_to_provider_endpointing_for_a_recognizer_that_endpoints() -> None:
    p = plan(load(variant(lambda d: d["turn"].update(strategy="auto"))), POOL)
    assert p.turn_detection == "stt"
