from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .enums import Channel, EgressLayout, ErrorCode, PrivacyMode, RecordingStart

if TYPE_CHECKING:
    from .config import ResolvedSessionConfig


@dataclass(frozen=True, slots=True)
class CrossFieldRule:
    broken: Callable[[ResolvedSessionConfig], bool]
    code: ErrorCode
    pointer: str
    because: str


CROSS_FIELD_RULES: tuple[CrossFieldRule, ...] = (
    CrossFieldRule(
        broken=lambda c: c.privacy_mode is PrivacyMode.SEALED and c.agent.enabled,
        code=ErrorCode.PRIVACY_MODE_FORBIDS,
        pointer="/agent/enabled",
        because=(
            "a sealed session cannot have an agent dispatched into it, because an agent "
            "that transcribes or responds must decrypt the audio"
        ),
    ),
    CrossFieldRule(
        broken=lambda c: c.recording.enabled and not c.recording.consent_artifact_id,
        code=ErrorCode.CONSENT_REQUIRED,
        pointer="/recording/consentArtifactId",
        because="recording cannot proceed without a consent artifact",
    ),
    CrossFieldRule(
        broken=lambda c: (
            c.recording.enabled
            and c.recording.start_at is RecordingStart.SESSION_CREATE
            and c.recording.layout is not EgressLayout.ROOM_COMPOSITE
        ),
        code=ErrorCode.INVALID_CONFIG,
        pointer="/recording/layout",
        because=(
            "capture at session creation needs a room composite, because a track egress "
            "attaches to a published track and cannot start before one exists"
        ),
    ),
    CrossFieldRule(
        broken=lambda c: c.channel is Channel.TELEPHONY and c.media.video.enabled,
        code=ErrorCode.INVALID_CONFIG,
        pointer="/media/video/enabled",
        because=(
            "telephony carries narrowband audio and no video at all, so a video profile "
            "on this channel describes a stream that cannot exist"
        ),
    ),
    CrossFieldRule(
        broken=lambda c: bool(c.media.video.scalability_mode) and not c.media.video.layered,
        code=ErrorCode.INVALID_CONFIG,
        pointer="/media/video/scalabilityMode",
        because=(
            "a scalability mode names spatial and temporal layers that only a layered "
            "codec produces, so with this codec it promises layering the session will not get"
        ),
    ),
    CrossFieldRule(
        broken=lambda c: (
            c.media.egress.preset is not None and c.media.egress.states_explicit_fields
        ),
        code=ErrorCode.INVALID_CONFIG,
        pointer="/media/egress/preset",
        because=(
            "a preset and explicit encode fields are two answers to one question, and the "
            "recording can only be encoded one way"
        ),
    ),
    CrossFieldRule(
        broken=lambda c: (
            c.recording.enabled and not c.media.video.enabled and c.media.egress.states_video
        ),
        code=ErrorCode.INVALID_CONFIG,
        pointer="/media/egress",
        because=(
            "the session publishes no video, so an egress profile that names a video size, "
            "framerate, bitrate, codec or preset describes an encode of a stream that does "
            "not exist"
        ),
    ),
)
