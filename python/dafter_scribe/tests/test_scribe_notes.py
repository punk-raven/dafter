from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from dafter_core.enums import EventType
from dafter_runtime.events import SessionEvents
from dafter_scribe.notes import (
    AgentNote,
    Notes,
    Unreadable,
    notes_payload,
    read_notes,
    with_note,
)
from scribe_stub import ASHA, RAVI, config

LABELS = ["Asha", "Agent"]
SPEAKERS = {"Asha": {"kind": "human", "participantId": ASHA}, "Agent": {"kind": "agent"}}
SOURCE = {"provider": "sarvam", "model": "sarvam-105b"}


def notes(**fields: object) -> Notes:
    return read_notes(json.dumps({"summary": "रिपोर्ट शुक्रवार तक।", **fields}), LABELS)


def test_the_notes_keep_what_the_schema_allows_and_drop_the_rest() -> None:
    got = notes(
        summary="  रिपोर्ट \n शुक्रवार तक।  ",
        decisions=["शुक्रवार", "", "शुक्रवार", 7, "x" * 400],
        actionItems=[{"task": "रिपोर्ट भेजना", "owner": "रवि", "due": "शुक्रवार"}, {"owner": "x"}, "y"],
        names=["रवि"] * 3,
        numbers=["12 लाख"],
        speakers=[
            {"label": "Asha", "points": ["शुक्रवार तय किया"]},
            {"label": "Mallory", "points": ["forged"]},
            {"label": "Agent", "points": []},
        ],
    )
    assert got.summary == "रिपोर्ट शुक्रवार तक।"
    assert got.decisions == ("शुक्रवार", "x" * 300)
    assert [a.to_dict() for a in got.action_items] == [
        {"task": "रिपोर्ट भेजना", "owner": "रवि", "due": "शुक्रवार"}
    ]
    assert got.names == ("रवि",)
    assert got.speakers == (("Asha", ("शुक्रवार तय किया",)),)


def test_bracketed_and_repeated_speaker_labels_are_read_as_one_speaker_each() -> None:
    sarvam_reply = {
        "speakers": [
            {"label": "[Asha]", "points": ["तिमाही रिपोर्ट कब तक भेजी जाएगी, यह पूछा।"]},
            {"label": "[Ravi]", "points": ["शुक्रवार तक तिमाही रिपोर्ट भेजने की बात कही।"]},
            {"label": "[Asha]", "points": ["बजट के बारे में आशा फाइनेंस से पूछेगी।"]},
            {"label": "आशा", "points": ["transliterated, so unknown"]},
        ],
        "actionItems": [{"task": "आशा फाइनेंस से पूछना", "owner": "आशा", "due": ""}],
    }
    got = read_notes(json.dumps({"summary": "s", **sarvam_reply}), ["Asha", "Ravi"])
    assert got.speakers == (
        ("Asha", ("तिमाही रिपोर्ट कब तक भेजी जाएगी, यह पूछा।", "बजट के बारे में आशा फाइनेंस से पूछेगी।")),
        ("Ravi", ("शुक्रवार तक तिमाही रिपोर्ट भेजने की बात कही।",)),
    )
    assert [a.to_dict() for a in got.action_items] == [{"task": "आशा फाइनेंस से पूछना", "owner": "आशा"}]


@pytest.mark.parametrize(
    "raw",
    ["not json", "[1, 2]", json.dumps({"decisions": ["x"]}), json.dumps({"summary": "   "})],
)
def test_notes_without_a_summary_are_unreadable(raw: str) -> None:
    with pytest.raises(Unreadable):
        read_notes(raw, LABELS)


def test_a_notes_payload_is_a_valid_envelope_with_opaque_speakers() -> None:
    cfg = config()

    async def publish(body: bytes) -> None:
        return None

    events = SessionEvents(cfg, publish, clock=lambda: datetime(2026, 9, 24, tzinfo=UTC))
    taken = with_note((), AgentNote("n_3f9a1c07b2e4d856", "रवि शुक्रवार तक रिपोर्ट भेजेगा", RAVI))
    taken = with_note(taken, AgentNote("n_3f9a1c07b2e4d856", "again", RAVI))
    payload = notes_payload(
        2, notes(speakers=[{"label": "Asha", "points": ["हाँ"]}]), SPEAKERS, taken, SOURCE
    )
    event = events.envelope(EventType.SCRIBE_NOTES, payload)
    assert event.payload["speakers"] == [
        {"speaker": {"kind": "human", "participantId": ASHA}, "points": ["हाँ"]}
    ]
    assert event.payload["notes"] == [
        {"noteId": "n_3f9a1c07b2e4d856", "text": "रवि शुक्रवार तक रिपोर्ट भेजेगा", "takenBy": RAVI}
    ]
    assert "Asha" not in json.dumps(event.payload)
