from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from dafter_core.enums import ErrorCode, EventType
from dafter_core.errors import DafterError
from dafter_core.events import parse_event
from dafter_core.hashing import hash_document

VECTORS = Path(__file__).resolve().parents[3] / "testdata" / "events"
PARTIAL = "transcript-partial.json"
FINAL = "transcript-final.json"
VERSION = "transcript-version-created.json"
TRANSCRIPT_VERSION_HASH = "4010587f3b7e5c5efc778288684a658a53c3266c6a572909d1b157b1d44efe9a"


def vector(name: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((VECTORS / name).read_text())
    return doc


@pytest.mark.parametrize(
    ("name", "typ"),
    [
        (PARTIAL, EventType.TRANSCRIPT_PARTIAL),
        (FINAL, EventType.TRANSCRIPT_FINAL),
        (VERSION, EventType.TRANSCRIPT_VERSION_CREATED),
    ],
)
def test_transcript_vectors_parse(name: str, typ: EventType) -> None:
    assert parse_event((VECTORS / name).read_bytes()).type is typ


def test_transcript_hash_is_pinned_across_both_halves() -> None:
    payload = vector(VERSION)["payload"]
    assert hash_document(json.dumps(payload), "transcriptHash") == TRANSCRIPT_VERSION_HASH
    assert payload["transcriptHash"] == TRANSCRIPT_VERSION_HASH


def _line(p: dict[str, Any], rendering: str, i: int) -> dict[str, Any]:
    line: dict[str, Any] = p[rendering][i]
    return line


MUTATIONS: list[tuple[str, str, Callable[[dict[str, Any]], object]]] = [
    ("human without a participant", PARTIAL, lambda p: p["speaker"].pop("participantId")),
    ("human named", PARTIAL, lambda p: p["speaker"].update(participantId="Asha")),
    (
        "agent with a participant id",
        FINAL,
        lambda p: p["speaker"].update(participantId="p_4b81e0d7"),
    ),
    ("unknown speaker kind", FINAL, lambda p: p["speaker"].update(kind="bot")),
    ("segment id not opaque", PARTIAL, lambda p: p.update(segmentId="segment-1")),
    ("missing text", PARTIAL, lambda p: p.pop("text")),
    ("source without a model", PARTIAL, lambda p: p["source"].pop("model")),
    ("unknown caption field", FINAL, lambda p: p.update(speakerName="Nivya")),
    ("not the batch pass", VERSION, lambda p: p.update(**{"pass": "realtime"})),
    ("no transcript hash", VERSION, lambda p: p.pop("transcriptHash")),
    ("no recordings", VERSION, lambda p: p["provenance"].update(recordings=[])),
    ("no config hash", VERSION, lambda p: p["provenance"].pop("configHash")),
    (
        "a recording the media server could not mint",
        VERSION,
        lambda p: _line(p, "verbatim", 0).update(recordingId="r_1d05fa73"),
    ),
    ("negative offset", VERSION, lambda p: _line(p, "clean", 1).update(startMs=-5)),
    ("no clean rendering", VERSION, lambda p: p.pop("clean")),
    ("line without a speaker", VERSION, lambda p: _line(p, "verbatim", 1).pop("speaker")),
]


@pytest.mark.parametrize(("name", "file", "mutate"), MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_transcript_payloads_are_enforced(
    name: str, file: str, mutate: Callable[[dict[str, Any]], object]
) -> None:
    doc = vector(file)
    mutate(doc["payload"])
    with pytest.raises(DafterError) as exc:
        parse_event(json.dumps(doc))
    assert exc.value.code is ErrorCode.INTERNAL, name
    assert exc.value.details, name
