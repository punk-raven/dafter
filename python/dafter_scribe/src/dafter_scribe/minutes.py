from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from .notes import (
    ACTION_ITEMS,
    ACTION_ITEMS_SCHEMA,
    AGENT_NOTES,
    DECISIONS,
    LINE,
    LINES_SCHEMA,
    QUESTIONS,
    ActionItem,
    AgentNote,
    Notes,
    UnreadableReplyError,
    action_items,
    arguments,
    lines,
    schema,
    text,
)

SUMMARY = 4000
MINUTES_TOOL = "submit_minutes"
MINUTES_SCHEMA = schema(
    MINUTES_TOOL,
    "Submit the minutes of the call.",
    {
        "summary": {"type": "string"},
        "decisions": LINES_SCHEMA,
        "actionItems": ACTION_ITEMS_SCHEMA,
        "openQuestions": LINES_SCHEMA,
    },
)


@dataclass(frozen=True, slots=True)
class Minutes:
    summary: str
    decisions: tuple[str, ...]
    action_items: tuple[ActionItem, ...]
    open_questions: tuple[str, ...]
    notes: tuple[AgentNote, ...]

    @classmethod
    def from_notes(cls, notes: Notes, taken: Sequence[AgentNote]) -> Minutes:
        return cls(
            notes.summary, notes.decisions, notes.action_items, notes.open_questions, tuple(taken)
        )


def read_minutes(raw: str, taken: Sequence[AgentNote]) -> Minutes:
    doc = arguments(raw)
    summary = text(doc.get("summary"), SUMMARY)
    if not summary:
        raise UnreadableReplyError("the minutes carry no summary")
    return Minutes(
        summary=summary,
        decisions=lines(doc.get("decisions"), LINE, DECISIONS),
        action_items=action_items(doc.get("actionItems"))[:ACTION_ITEMS],
        open_questions=lines(doc.get("openQuestions"), LINE, QUESTIONS),
        notes=tuple(taken),
    )


def minutes_payload(
    minutes: Minutes,
    final: bool,
    cost: tuple[float, int],
    quality: dict[str, Any] | None,
    source: dict[str, str],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "final": final,
        "summary": minutes.summary,
        "decisions": list(minutes.decisions),
        "actionItems": [a.to_dict() for a in minutes.action_items],
        "openQuestions": list(minutes.open_questions),
        "notes": [n.to_dict() for n in minutes.notes[-AGENT_NOTES:]],
        "costInr": cost[0],
        "unpricedItems": cost[1],
        "source": source,
    }
    if quality is not None:
        payload["quality"] = quality
    return payload


__all__ = ["MINUTES_SCHEMA", "MINUTES_TOOL", "Minutes", "minutes_payload", "read_minutes"]
