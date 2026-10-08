from __future__ import annotations

from enum import StrEnum

import pytest
from dafter_core import (
    AddressingMode,
    AgentMode,
    AgentState,
    CallerCheck,
    Channel,
    EgressLayout,
    EgressPreset,
    EgressVideoCodec,
    EncryptionMode,
    ErrorCode,
    EventType,
    KeyModel,
    NoiseCancellation,
    PhoneGuests,
    PrivacyMode,
    RecordingNotice,
    RecordingStart,
    Role,
    Situation,
    SpeakerKind,
    SpeechNormalization,
    Stage,
    TranscriptionMode,
    TurnStrategy,
    UsageUnit,
    VideoCodec,
    VideoResolution,
    WakeSource,
    enums,
    schemas,
)

CFG = schemas.RESOLVED_SESSION_CONFIG

CASES = [
    (ErrorCode, schemas.ERROR, ("$defs", "ErrorCode", "enum")),
    (Stage, schemas.ERROR, ("properties", "stage", "enum")),
    (EventType, schemas.EVENT_ENVELOPE, ("$defs", "EventType", "enum")),
    (AgentState, schemas.EVENT_ENVELOPE, ("$defs", "AgentState", "enum")),
    (UsageUnit, schemas.EVENT_ENVELOPE, ("$defs", "UsageUnit", "enum")),
    (WakeSource, schemas.EVENT_ENVELOPE, ("$defs", "WakeSource", "enum")),
    (SpeakerKind, schemas.EVENT_ENVELOPE, ("$defs", "SpeakerKind", "enum")),
    (Role, schemas.IDS, ("$defs", "Role", "enum")),
    (Channel, schemas.IDS, ("$defs", "Channel", "enum")),
    (PrivacyMode, CFG, ("properties", "privacyMode", "enum")),
    (AgentMode, CFG, ("properties", "agent", "properties", "mode", "enum")),
    (AddressingMode, CFG, ("$defs", "Addressing", "properties", "mode", "enum")),
    (TurnStrategy, CFG, ("$defs", "Turn", "properties", "strategy", "enum")),
    (VideoCodec, CFG, ("$defs", "VideoProfile", "properties", "codec", "enum")),
    (VideoResolution, CFG, ("$defs", "VideoProfile", "properties", "resolution", "enum")),
    (
        NoiseCancellation,
        CFG,
        ("$defs", "AudioProfile", "properties", "noiseCancellation", "enum"),
    ),
    (EgressPreset, CFG, ("$defs", "EgressProfile", "properties", "preset", "enum")),
    (EgressVideoCodec, CFG, ("$defs", "EgressProfile", "properties", "videoCodec", "enum")),
    (EncryptionMode, CFG, ("$defs", "EncryptionProfile", "properties", "mode", "enum")),
    (KeyModel, CFG, ("$defs", "EncryptionProfile", "properties", "keyModel", "enum")),
    (EgressLayout, CFG, ("$defs", "Recording", "properties", "layout", "enum")),
    (RecordingStart, CFG, ("$defs", "Recording", "properties", "startAt", "enum")),
    (TranscriptionMode, CFG, ("$defs", "Transcription", "properties", "mode", "enum")),
    (
        RecordingNotice,
        schemas.TELEPHONY,
        ("$defs", "Telephony", "properties", "recordingNotice", "enum"),
    ),
    (SpeechNormalization, CFG, ("$defs", "Speech", "properties", "normalization", "enum")),
    (Situation, CFG, ("$defs", "Situation", "enum")),
    (
        PhoneGuests,
        schemas.TELEPHONY,
        ("$defs", "Telephony", "properties", "phoneGuests", "enum"),
    ),
    (
        CallerCheck,
        schemas.TELEPHONY,
        ("$defs", "DialIn", "properties", "callerCheck", "enum"),
    ),
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
