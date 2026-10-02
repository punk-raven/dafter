from __future__ import annotations

import json
from typing import Any

import pytest
from dafter_core.config import parse
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError

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


def doc(switching: dict[str, Any] | None) -> str:
    d = json.loads(json.dumps(MINIMAL))
    if switching is not None:
        d["agent"]["languageSwitching"] = switching
    return json.dumps(d)


def refused_at(raw: str, pointer: str) -> None:
    with pytest.raises(DafterError) as exc:
        parse(raw)
    assert exc.value.code is ErrorCode.INVALID_CONFIG
    assert any(f"'{pointer}" in d for d in exc.value.details), exc.value.details


def test_a_session_without_the_block_never_switches() -> None:
    s = parse(doc(None)).agent.language_switching
    assert (s.enabled, s.languages, s.min_confidence, s.min_words) == (False, (), 0.8, 3)


def test_a_session_may_switch_between_languages_that_include_its_own() -> None:
    s = parse(
        doc(
            {
                "enabled": True,
                "languages": ["en-IN", "hi", "kn-IN"],
                "minConfidence": 0.7,
                "minWords": 2,
            }
        )
    ).agent.language_switching
    assert (s.enabled, s.languages, s.min_confidence, s.min_words) == (
        True,
        ("en-IN", "hi", "kn-IN"),
        0.7,
        2,
    )
    assert not parse(doc({"enabled": False, "languages": ["hi"]})).agent.language_switching.enabled


@pytest.mark.parametrize(
    "switching",
    [{"enabled": True, "languages": ["hi", "kn-IN"]}, {"enabled": True}],
    ids=["other languages only", "no languages"],
)
def test_switching_that_leaves_out_the_session_language_is_refused(
    switching: dict[str, Any],
) -> None:
    refused_at(doc(switching), "/agent/languageSwitching/languages")


@pytest.mark.parametrize(
    ("pointer", "switching"),
    [
        (
            "/agent/languageSwitching/minConfidence",
            {"enabled": True, "languages": ["en-IN"], "minConfidence": 1.5},
        ),
        (
            "/agent/languageSwitching/minWords",
            {"enabled": True, "languages": ["en-IN"], "minWords": 0},
        ),
        ("/agent/languageSwitching/languages/0", {"enabled": True, "languages": ["English"]}),
        (
            "/agent/languageSwitching",
            {"enabled": True, "languages": ["en-IN"], "detect": "always"},
        ),
    ],
)
def test_switching_bounds_are_stated_in_the_schema(pointer: str, switching: dict[str, Any]) -> None:
    refused_at(doc(switching), pointer)
