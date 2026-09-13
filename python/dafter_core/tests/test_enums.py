"""The generated enums must match the schemas they came from.

The Go half runs the same checks against the same files. A generator bug passes
generate-check, because the committed output matches what the generator
produces, and fails here, because that output no longer matches the schema.
"""

from __future__ import annotations

from enum import StrEnum

import pytest

from dafter_core import (
    AgentMode,
    AgentState,
    Channel,
    EgressLayout,
    ErrorCode,
    EventType,
    PrivacyMode,
    RecordingStart,
    Role,
    Stage,
    TurnStrategy,
    enums,
    schemas,
)

CFG = schemas.RESOLVED_SESSION_CONFIG

CASES = [
    (ErrorCode, schemas.ERROR, ("$defs", "ErrorCode", "enum")),
    (Stage, schemas.ERROR, ("properties", "stage", "enum")),
    (EventType, schemas.EVENT_ENVELOPE, ("$defs", "EventType", "enum")),
    (AgentState, schemas.EVENT_ENVELOPE, ("$defs", "AgentState", "enum")),
    (Role, schemas.IDS, ("$defs", "Role", "enum")),
    (Channel, schemas.IDS, ("$defs", "Channel", "enum")),
    (PrivacyMode, CFG, ("properties", "privacyMode", "enum")),
    (AgentMode, CFG, ("properties", "agent", "properties", "mode", "enum")),
    (TurnStrategy, CFG, ("$defs", "Turn", "properties", "strategy", "enum")),
    (EgressLayout, CFG, ("$defs", "Recording", "properties", "layout", "enum")),
    (RecordingStart, CFG, ("$defs", "Recording", "properties", "startAt", "enum")),
]


@pytest.mark.parametrize(
    ("enum_cls", "schema_file", "pointer"), CASES, ids=[c[0].__name__ for c in CASES]
)
def test_enum_matches_schema(
    enum_cls: type[StrEnum], schema_file: str, pointer: tuple[str, ...]
) -> None:
    assert {str(m) for m in enum_cls} == schemas.enum_at(schema_file, *pointer)


def test_every_generated_enum_has_a_drift_test() -> None:
    generated = {
        name
        for name, obj in vars(enums).items()
        if isinstance(obj, type) and issubclass(obj, StrEnum) and obj is not StrEnum
    }
    assert generated == {c[0].__name__ for c in CASES}
