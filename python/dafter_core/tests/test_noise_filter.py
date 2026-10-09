from __future__ import annotations

import json
from typing import Any

import pytest
from dafter_core.config import parse
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError
from dafter_core.pipeline import NO_NOISE_FILTER

MINIMAL: dict[str, Any] = {
    "apiVersion": "dafter.dev/v1",
    "sessionId": "s_7f3a9c21",
    "tenantId": "t_9c21a4be",
    "privacyMode": "open",
    "language": "en-IN",
    "channel": "webrtc",
    "agent": {"enabled": True, "pool": "dafter-py"},
    "turn": {"strategy": "auto"},
    "recording": {"enabled": False},
    "budgets": {"turnGapP50Ms": 800, "turnGapP95Ms": 1500},
}


def doc(pipeline: dict[str, Any]) -> str:
    d = json.loads(json.dumps(MINIMAL))
    d["agent"]["pipeline"] = pipeline
    return json.dumps(d)


def test_a_pipeline_that_names_no_noise_filter_runs_none() -> None:
    pipeline = parse(doc({"vad": {"provider": "silero"}})).agent.pipeline
    assert pipeline is not None
    assert pipeline.noise_filter == NO_NOISE_FILTER == "off"


@pytest.mark.parametrize("name", ["off", "nc", "bvc", "bvc_telephony"])
def test_a_pipeline_names_each_noise_filter_the_schema_allows(name: str) -> None:
    pipeline = parse(doc({"noiseFilter": name})).agent.pipeline
    assert pipeline is not None
    assert pipeline.noise_filter == name


@pytest.mark.parametrize("name", ["krisp", "rnnoise", True])
def test_a_noise_filter_the_schema_does_not_allow_is_refused(name: object) -> None:
    with pytest.raises(DafterError) as exc:
        parse(doc({"noiseFilter": name}))
    assert exc.value.code is ErrorCode.INVALID_CONFIG
    assert any("/agent/pipeline/noiseFilter" in d for d in exc.value.details), exc.value.details
