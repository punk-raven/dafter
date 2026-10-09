from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from dafter_core.config import parse
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError
from dafter_core.versioning import UNVERSIONED, Version, version_label

VECTORS = Path(__file__).resolve().parents[3] / "testdata" / "telephony" / "rules.json"
BASE: dict[str, Any] = json.loads(VECTORS.read_text())["base"]


def document(**patch: Any) -> str:
    return json.dumps({**BASE, **patch})


def test_a_session_carries_the_version_it_runs() -> None:
    cfg = parse(document(version={"id": "support-v4", "candidate": True}))
    assert cfg.version == Version(id="support-v4", candidate=True)
    assert cfg.version.arm == "candidate"
    assert version_label(cfg.version) == "support-v4"


def test_a_stable_session_states_no_arm() -> None:
    cfg = parse(document(version={"id": "support-v3"}))
    assert cfg.version is not None
    assert cfg.version.arm == "stable"


def test_a_session_without_a_version_is_labelled_unversioned() -> None:
    cfg = parse(document())
    assert cfg.version is None
    assert version_label(cfg.version) == UNVERSIONED


@pytest.mark.parametrize(
    "version",
    [{"id": "Support V4"}, {"id": ""}, {"candidate": True}, {"id": "v4", "percent": 5}],
    ids=["spaces", "empty", "no id", "unknown field"],
)
def test_a_malformed_version_is_refused(version: dict[str, Any]) -> None:
    with pytest.raises(DafterError) as caught:
        parse(document(version=version))
    assert caught.value.code is ErrorCode.INVALID_CONFIG


def test_a_canary_never_reaches_a_resolved_document() -> None:
    with pytest.raises(DafterError):
        parse(document(canary={"profile": "support-v4", "percent": 5}))
