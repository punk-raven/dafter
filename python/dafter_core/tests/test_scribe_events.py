from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from dafter_core.enums import ErrorCode, EventType
from dafter_core.errors import DafterError
from dafter_core.events import parse_event

VECTORS = Path(__file__).resolve().parents[3] / "testdata" / "events"
NOTES = "scribe-notes.json"
MINUTES = "scribe-minutes.json"
TAKEN = "agent-note-taken.json"
SCORED = "agent-turn-scored.json"

Mutate = Callable[[dict[str, Any]], None]


def vector(name: str) -> dict[str, Any]:
    doc: dict[str, Any] = json.loads((VECTORS / name).read_text())
    return doc


@pytest.mark.parametrize(
    ("name", "typ"),
    [
        (NOTES, EventType.SCRIBE_NOTES),
        (MINUTES, EventType.SCRIBE_MINUTES),
        (TAKEN, EventType.AGENT_NOTE_TAKEN),
        (SCORED, EventType.AGENT_TURN_SCORED),
    ],
)
def test_scribe_vectors_parse(name: str, typ: EventType) -> None:
    assert parse_event((VECTORS / name).read_bytes()).type is typ


def first(p: dict[str, Any], key: str) -> dict[str, Any]:
    item: dict[str, Any] = p[key][0]
    return item


MUTATIONS: list[tuple[str, str, Mutate]] = [
    ("notes without a revision", NOTES, lambda p: p.pop("revision")),
    ("notes at revision zero", NOTES, lambda p: p.update(revision=0)),
    ("action item without a task", NOTES, lambda p: first(p, "actionItems").pop("task")),
    (
        "speaker named rather than opaque",
        NOTES,
        lambda p: first(p, "speakers")["speaker"].update(participantId="Ravi"),
    ),
    ("speaker with no points", NOTES, lambda p: first(p, "speakers").update(points=[])),
    ("note id not opaque", NOTES, lambda p: first(p, "notes").update(noteId="note-1")),
    ("notes without their llm", NOTES, lambda p: p.pop("source")),
    ("unknown notes field", NOTES, lambda p: p.update(transcript="everything")),
    ("minutes without a cost", MINUTES, lambda p: p.pop("costInr")),
    ("minutes with a score above one", MINUTES, lambda p: p["quality"].update(meanScore=1.5)),
    ("note taken without text", TAKEN, lambda p: p.pop("text")),
    ("note taken by a name", TAKEN, lambda p: p.update(takenBy="Asha")),
    ("score without criteria", SCORED, lambda p: p.pop("criteria")),
    ("score and error together", SCORED, lambda p: p.update(error="provider_timeout")),
    ("a verdict the judge cannot give", SCORED, lambda p: p["criteria"].update(language="great")),
    ("scored turn quoting the reply", SCORED, lambda p: p.update(text="जी")),
    (
        "scored turn version without an arm",
        SCORED,
        lambda p: p.update(configVersion={"id": "support-v3"}),
    ),
]


@pytest.mark.parametrize(("name", "file", "mutate"), MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_scribe_payloads_are_enforced(name: str, file: str, mutate: Mutate) -> None:
    doc = vector(file)
    mutate(doc["payload"])
    with pytest.raises(DafterError) as caught:
        parse_event(json.dumps(doc))
    assert caught.value.code is ErrorCode.INTERNAL
    assert caught.value.details


def test_a_judge_that_failed_says_why_instead_of_scoring() -> None:
    doc = vector(SCORED)
    p = doc["payload"]
    del p["score"], p["criteria"]
    p["error"] = "provider_timeout"
    assert parse_event(json.dumps(doc)).payload["error"] == "provider_timeout"


def test_a_scored_turn_carries_the_version_the_session_runs() -> None:
    doc = vector(SCORED)
    doc["payload"]["configVersion"] = {"id": "support-v4", "arm": "candidate"}
    parsed = parse_event(json.dumps(doc)).payload
    assert parsed["configVersion"] == {"id": "support-v4", "arm": "candidate"}
