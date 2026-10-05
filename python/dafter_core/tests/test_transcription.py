from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from dafter_core.config import parse
from dafter_core.enums import ErrorCode, TranscriptionMode
from dafter_core.errors import DafterError

VECTORS = Path(__file__).resolve().parents[3] / "testdata" / "transcription" / "rules.json"
FILE: dict[str, Any] = json.loads(VECTORS.read_text())
POINTER = re.compile(r"^at '([^']*)'")


def document(case: dict[str, Any]) -> str:
    doc = {**FILE["base"], **json.loads(json.dumps(case["patch"]))}
    transcription = doc.get("transcription")
    if isinstance(transcription, dict) and transcription.get("batch") == "$batch":
        transcription["batch"] = FILE["batch"]
    return json.dumps(doc)


@pytest.mark.parametrize("case", FILE["cases"], ids=[c["name"] for c in FILE["cases"]])
def test_transcription_rules_match_the_shared_vectors(case: dict[str, Any]) -> None:
    rejected = case["rejected"]
    if rejected is None:
        cfg = parse(document(case))
        assert cfg.transcription.mode is TranscriptionMode(case["mode"])
        return
    with pytest.raises(DafterError) as caught:
        parse(document(case))
    assert caught.value.code is ErrorCode(rejected["code"])
    pointers = [m.group(1) for d in caught.value.details if (m := POINTER.match(d))]
    assert pointers == rejected["pointers"]


@pytest.mark.parametrize(
    ("mode", "transcribes", "live", "after_call"),
    [
        (TranscriptionMode.OFF, False, False, False),
        (TranscriptionMode.LIVE, True, True, False),
        (TranscriptionMode.AFTER_CALL, True, False, True),
        (TranscriptionMode.BOTH, True, True, True),
    ],
)
def test_transcription_modes_say_what_they_run(
    mode: TranscriptionMode, transcribes: bool, live: bool, after_call: bool
) -> None:
    case = {
        "patch": {"transcription": {"mode": str(mode), "consentArtifactId": "c", "batch": "$batch"}}
    }
    t = parse(document(case)).transcription
    assert (t.transcribes, t.live, t.after_call) == (transcribes, live, after_call)
    assert t.batch is not None and t.batch.model == "saaras:v3"
