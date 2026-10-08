from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

LINE = 300
SUMMARY = 2000
NAME = 100
NOTE = 500
DECISIONS = 50
ACTION_ITEMS = 50
QUESTIONS = 30
MENTIONS = 50
SPEAKERS = 50
POINTS = 5
AGENT_NOTES = 100

NOTES_TOOL = "submit_notes"

LINES_SCHEMA = {"type": "array", "items": {"type": "string"}}
ACTION_ITEMS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "task": {"type": "string"},
            "owner": {
                "type": "string",
                "description": "Who will do it, as said. Leave the field out if nobody was named.",
            },
            "due": {
                "type": "string",
                "description": "When, as said. Leave the field out if no time was said.",
            },
        },
        "required": ["task"],
    },
}


def schema(name: str, description: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties, "required": list(properties)},
    }


NOTES_SCHEMA = schema(
    NOTES_TOOL,
    "Submit the notes of the whole call so far.",
    {
        "summary": {"type": "string"},
        "decisions": LINES_SCHEMA,
        "actionItems": ACTION_ITEMS_SCHEMA,
        "openQuestions": LINES_SCHEMA,
        "names": LINES_SCHEMA,
        "numbers": LINES_SCHEMA,
        "speakers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {
                        "type": "string",
                        "description": "Exactly as inside the transcript's square brackets.",
                    },
                    "points": LINES_SCHEMA,
                },
                "required": ["label", "points"],
            },
        },
    },
)


class UnreadableReplyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ActionItem:
    task: str
    owner: str = ""
    due: str = ""

    def to_dict(self) -> dict[str, str]:
        d = {"task": self.task}
        if self.owner:
            d["owner"] = self.owner
        if self.due:
            d["due"] = self.due
        return d


@dataclass(frozen=True, slots=True)
class AgentNote:
    note_id: str
    text: str
    taken_by: str | None = None

    def to_dict(self) -> dict[str, str]:
        d = {"noteId": self.note_id, "text": self.text}
        if self.taken_by:
            d["takenBy"] = self.taken_by
        return d


@dataclass(frozen=True, slots=True)
class Notes:
    summary: str = ""
    decisions: tuple[str, ...] = ()
    action_items: tuple[ActionItem, ...] = ()
    open_questions: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    numbers: tuple[str, ...] = ()
    speakers: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def prompt(self) -> str:
        return json.dumps(
            {
                "summary": self.summary,
                "decisions": list(self.decisions),
                "actionItems": [a.to_dict() for a in self.action_items],
                "openQuestions": list(self.open_questions),
                "names": list(self.names),
                "numbers": list(self.numbers),
                "speakers": [{"label": s, "points": list(p)} for s, p in self.speakers],
            },
            ensure_ascii=False,
        )


def text(value: object, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def lines(value: object, limit: int, count: int) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    kept = (text(v, limit) for v in value)
    return tuple(dict.fromkeys(v for v in kept if v))[:count]


def action_items(value: object) -> tuple[ActionItem, ...]:
    if not isinstance(value, list):
        return ()
    items = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        task = text(raw.get("task"), LINE)
        if task:
            items.append(ActionItem(task, text(raw.get("owner"), NAME), text(raw.get("due"), NAME)))
    return tuple(items[:ACTION_ITEMS])


def _speakers(value: object, labels: Sequence[str]) -> tuple[tuple[str, tuple[str, ...]], ...]:
    if not isinstance(value, list):
        return ()
    found: dict[str, tuple[str, ...]] = {}
    for raw in value:
        if not isinstance(raw, dict):
            continue
        label = text(raw.get("label"), NAME).strip("[] ")
        points = lines(raw.get("points"), LINE, POINTS)
        if label in labels and points:
            found[label] = tuple(dict.fromkeys((*found.get(label, ()), *points)))[:POINTS]
    return tuple(found.items())[:SPEAKERS]


def arguments(raw: str) -> dict[str, Any]:
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise UnreadableReplyError("the arguments are not JSON") from exc
    if not isinstance(doc, dict):
        raise UnreadableReplyError("the arguments are not an object")
    return doc


def read_notes(raw: str, labels: Sequence[str]) -> Notes:
    doc = arguments(raw)
    summary = text(doc.get("summary"), SUMMARY)
    if not summary:
        raise UnreadableReplyError("the notes carry no summary")
    return Notes(
        summary=summary,
        decisions=lines(doc.get("decisions"), LINE, DECISIONS),
        action_items=action_items(doc.get("actionItems")),
        open_questions=lines(doc.get("openQuestions"), LINE, QUESTIONS),
        names=lines(doc.get("names"), NAME, MENTIONS),
        numbers=lines(doc.get("numbers"), NAME, MENTIONS),
        speakers=_speakers(doc.get("speakers"), labels),
    )


def notes_payload(
    revision: int,
    notes: Notes,
    speakers: Mapping[str, dict[str, str]],
    taken: Sequence[AgentNote],
    source: dict[str, str],
) -> dict[str, Any]:
    return {
        "revision": revision,
        "summary": notes.summary,
        "decisions": list(notes.decisions),
        "actionItems": [a.to_dict() for a in notes.action_items],
        "openQuestions": list(notes.open_questions),
        "mentions": {"names": list(notes.names), "numbers": list(notes.numbers)},
        "speakers": [
            {"speaker": speakers[label], "points": list(points)}
            for label, points in notes.speakers
            if label in speakers
        ],
        "notes": [n.to_dict() for n in list(taken)[-AGENT_NOTES:]],
        "source": source,
    }


def with_note(taken: tuple[AgentNote, ...], note: AgentNote) -> tuple[AgentNote, ...]:
    if any(n.note_id == note.note_id for n in taken):
        return taken
    return (*taken, replace(note, text=text(note.text, NOTE)))


__all__ = [
    "ACTION_ITEMS_SCHEMA",
    "LINES_SCHEMA",
    "NOTES_SCHEMA",
    "NOTES_TOOL",
    "ActionItem",
    "AgentNote",
    "Notes",
    "UnreadableReplyError",
    "action_items",
    "arguments",
    "lines",
    "notes_payload",
    "read_notes",
    "schema",
    "text",
    "with_note",
]
