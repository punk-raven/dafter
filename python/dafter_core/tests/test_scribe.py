from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from dafter_core.config import parse
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError

VECTORS = Path(__file__).resolve().parents[3] / "testdata" / "scribe" / "rules.json"
FILE: dict[str, Any] = json.loads(VECTORS.read_text())
POINTER = re.compile(r"^at '([^']*)'")


def document(case: dict[str, Any]) -> str:
    doc = {**FILE["base"], **json.loads(json.dumps(case["patch"]))}
    scribe = doc.get("scribe")
    if isinstance(scribe, dict):
        for field in ("llm", "judge"):
            if scribe.get(field) == "$llm":
                scribe[field] = FILE["llm"]
    return json.dumps(doc)


@pytest.mark.parametrize("case", FILE["cases"], ids=[c["name"] for c in FILE["cases"]])
def test_scribe_rules_match_the_shared_vectors(case: dict[str, Any]) -> None:
    rejected = case["rejected"]
    if rejected is None:
        cfg = parse(document(case))
        assert (cfg.scribe.enabled, cfg.scribe.pool) == (case["enabled"], case["pool"])
        return
    with pytest.raises(DafterError) as caught:
        parse(document(case))
    assert caught.value.code is ErrorCode(rejected["code"])
    pointers = [m.group(1) for d in caught.value.details if (m := POINTER.match(d))]
    assert pointers == rejected["pointers"]


def test_a_scribe_states_its_defaults_when_the_document_leaves_them_out() -> None:
    case = {"patch": {"scribe": {"enabled": True, "consentArtifactId": "c", "llm": "$llm"}}}
    scribe = parse(document(case)).scribe
    assert (scribe.summary_interval_ms, scribe.after_call_timeout_seconds) == (60000, 900)
    assert scribe.judge is None
    assert scribe.llm is not None and scribe.llm.model == "sarvam-105b"


def test_scribe_scoring_is_off_and_capped_by_default() -> None:
    case = {"patch": {"scribe": {"enabled": True, "consentArtifactId": "c", "llm": "$llm"}}}
    scoring = parse(document(case)).scribe.scoring
    assert (scoring.sample_rate, scoring.max_turns_per_session) == (0.0, 20)
    assert not scoring.keep_failures and scoring.rate_for("hi") == 0.0


def test_scribe_scoring_reads_a_rate_per_base_language() -> None:
    scribe = {
        "enabled": True,
        "consentArtifactId": "c",
        "llm": "$llm",
        "scoring": {"sampleRate": 0.05, "languageSampleRates": {"kn": 0.25}, "keepFailures": True},
    }
    scoring = parse(document({"patch": {"scribe": scribe}})).scribe.scoring
    assert scoring.rate_for("kn-IN") == 0.25 and scoring.rate_for("hi") == 0.05
    assert scoring.keep_failures


@pytest.mark.parametrize(
    ("scoring", "pointer"),
    [
        ({"sampleRate": 1.5}, "/scribe/scoring/sampleRate"),
        ({"maxTurnsPerSession": 0}, "/scribe/scoring/maxTurnsPerSession"),
        ({"languageSampleRates": {"hi-IN": 0.5}}, "/scribe/scoring/languageSampleRates"),
        ({"everyTurn": True}, "/scribe/scoring"),
    ],
)
def test_scribe_scoring_refuses_a_rate_or_cap_out_of_range(
    scoring: dict[str, Any], pointer: str
) -> None:
    scribe = {"enabled": True, "consentArtifactId": "c", "llm": "$llm", "scoring": scoring}
    with pytest.raises(DafterError) as caught:
        parse(document({"patch": {"scribe": scribe}}))
    assert caught.value.code is ErrorCode.INVALID_CONFIG
    assert any(pointer in d for d in caught.value.details), caught.value.details
