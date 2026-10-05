from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from dafter_core.config import parse
from dafter_core.enums import CallerCheck, ErrorCode, RecordingNotice
from dafter_core.errors import DafterError

VECTORS = Path(__file__).resolve().parents[3] / "testdata" / "telephony" / "rules.json"
FILE: dict[str, Any] = json.loads(VECTORS.read_text())
POINTER = re.compile(r"^at '([^']*)'")


def document(case: dict[str, Any]) -> str:
    return json.dumps({**FILE["base"], **case["patch"]})


@pytest.mark.parametrize("case", FILE["cases"], ids=[c["name"] for c in FILE["cases"]])
def test_telephony_rules_match_the_shared_vectors(case: dict[str, Any]) -> None:
    rejected = case["rejected"]
    if rejected is None:
        cfg = parse(document(case))
        assert cfg.telephony.recording_notice is RecordingNotice(case["notice"])
        assert (cfg.telephony.trunk or "") == case["trunk"]
        if "callerCheck" in case:
            assert cfg.telephony.caller_check is CallerCheck(case["callerCheck"])
        return
    with pytest.raises(DafterError) as caught:
        parse(document(case))
    assert caught.value.code is ErrorCode(rejected["code"])
    pointers = [m.group(1) for d in caught.value.details if (m := POINTER.match(d))]
    assert pointers == rejected["pointers"]


def test_a_call_rings_and_lasts_what_the_session_states_or_the_defaults() -> None:
    bare = parse(document({"patch": {}})).telephony
    assert (bare.ringing_timeout_seconds, bare.max_call_duration_seconds) == (30, 1800)
    stated = parse(
        document(
            {"patch": {"telephony": {"ringingTimeoutSeconds": 20, "maxCallDurationSeconds": 600}}}
        )
    ).telephony
    assert (stated.ringing_timeout_seconds, stated.max_call_duration_seconds) == (20, 600)


def test_a_trunk_is_a_name_never_an_address() -> None:
    with pytest.raises(DafterError) as caught:
        parse(document({"patch": {"telephony": {"trunk": "sip:carrier.example:5060"}}}))
    assert caught.value.code is ErrorCode.INVALID_CONFIG
    assert any("/telephony/trunk" in d for d in caught.value.details)
