from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from . import schemas
from .enums import (
    AgentMode,
    Channel,
    EgressLayout,
    ErrorCode,
    PrivacyMode,
    RecordingStart,
    TurnStrategy,
)
from .errors import DafterError
from .validation import validate_document


@dataclass(frozen=True, slots=True)
class ProviderRef:
    provider: str
    model: str | None = None
    region: str | None = None
    credential_ref: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProviderRef:
        return cls(
            provider=d["provider"],
            model=d.get("model"),
            region=d.get("region"),
            credential_ref=d.get("credentialRef"),
            options=d.get("options") or {},
        )


@dataclass(frozen=True, slots=True)
class Pipeline:
    vad: ProviderRef | None = None
    stt: ProviderRef | None = None
    llm: ProviderRef | None = None
    tts: ProviderRef | None = None
    mt: ProviderRef | None = None
    realtime: ProviderRef | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Pipeline:
        def ref(key: str) -> ProviderRef | None:
            v = d.get(key)
            return ProviderRef.from_dict(v) if v else None

        return cls(*(ref(k) for k in ("vad", "stt", "llm", "tts", "mt", "realtime")))


@dataclass(frozen=True, slots=True)
class Interruption:
    enabled: bool = True
    min_duration_ms: int = 0
    min_words: int = 0
    false_interruption_timeout_ms: int = 0
    resume_false_interruption: bool = True

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Interruption:
        return cls(
            enabled=d.get("enabled", True),
            min_duration_ms=d.get("minDurationMs", 0),
            min_words=d.get("minWords", 0),
            false_interruption_timeout_ms=d.get("falseInterruptionTimeoutMs", 0),
            resume_false_interruption=d.get("resumeFalseInterruption", True),
        )


@dataclass(frozen=True, slots=True)
class Turn:
    strategy: TurnStrategy
    silence_ms: int = 0
    min_speech_ms: int = 0
    endpointing_delay_ms: int = 0
    local_vad_enabled: bool = True
    interruption: Interruption = field(default_factory=Interruption)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Turn:
        return cls(
            strategy=TurnStrategy(d["strategy"]),
            silence_ms=d.get("silenceMs", 0),
            min_speech_ms=d.get("minSpeechMs", 0),
            endpointing_delay_ms=d.get("endpointingDelayMs", 0),
            local_vad_enabled=d.get("localVadEnabled", True),
            interruption=Interruption.from_dict(d.get("interruption") or {}),
        )


@dataclass(frozen=True, slots=True)
class Agent:
    enabled: bool
    pool: str
    mode: AgentMode = AgentMode.CASCADED
    persona_ref: str | None = None
    pipeline: Pipeline | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Agent:
        return cls(
            enabled=d["enabled"],
            pool=d["pool"],
            mode=AgentMode(d.get("mode", AgentMode.CASCADED)),
            persona_ref=d.get("personaRef"),
            pipeline=Pipeline.from_dict(d["pipeline"]) if d.get("pipeline") else None,
        )


@dataclass(frozen=True, slots=True)
class Recording:
    enabled: bool
    layout: EgressLayout = EgressLayout.TRACK
    start_at: RecordingStart = RecordingStart.FIRST_PUBLISH
    retention_class: str | None = None
    consent_artifact_id: str | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Recording:
        return cls(
            enabled=d["enabled"],
            layout=EgressLayout(d.get("layout", EgressLayout.TRACK)),
            start_at=RecordingStart(d.get("startAt", RecordingStart.FIRST_PUBLISH)),
            retention_class=d.get("retentionClass"),
            consent_artifact_id=d.get("consentArtifactId"),
        )


@dataclass(frozen=True, slots=True)
class Budgets:
    turn_gap_p50_ms: int
    turn_gap_p95_ms: int
    max_session_cost_usd: float | None = None

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Budgets:
        return cls(
            turn_gap_p50_ms=d["turnGapP50Ms"],
            turn_gap_p95_ms=d["turnGapP95Ms"],
            max_session_cost_usd=d.get("maxSessionCostUsd"),
        )


@dataclass(frozen=True, slots=True)
class ResolvedSessionConfig:
    api_version: str
    session_id: str
    tenant_id: str
    privacy_mode: PrivacyMode
    language: str
    channel: Channel
    agent: Agent
    turn: Turn
    recording: Recording
    budgets: Budgets
    config_hash: str | None = None
    allowed_regions: tuple[str, ...] = ()

    def check(self) -> None:
        if self.privacy_mode is PrivacyMode.SEALED and self.agent.enabled:
            raise DafterError(
                ErrorCode.PRIVACY_MODE_FORBIDS,
                f"session {self.session_id} is sealed, so an agent cannot be dispatched into it",
            )
        if self.recording.enabled and not self.recording.consent_artifact_id:
            raise DafterError(
                ErrorCode.CONSENT_REQUIRED,
                f"session {self.session_id} enables recording without a consent artifact",
            )
        if (
            self.recording.enabled
            and self.recording.start_at is RecordingStart.SESSION_CREATE
            and self.recording.layout is not EgressLayout.ROOM_COMPOSITE
        ):
            raise DafterError(
                ErrorCode.INVALID_CONFIG,
                f"session {self.session_id} asks for capture at session creation with layout "
                f"{self.recording.layout!r}, but a track egress attaches to a published track "
                f"and cannot start before one exists",
            )


def parse(raw: bytes | str) -> ResolvedSessionConfig:
    if isinstance(raw, str):
        raw = raw.encode()
    doc = validate_document(schemas.RESOLVED_SESSION_CONFIG, raw, ErrorCode.INVALID_CONFIG)
    residency = doc.get("residency") or {}
    cfg = ResolvedSessionConfig(
        api_version=doc["apiVersion"],
        session_id=doc["sessionId"],
        tenant_id=doc["tenantId"],
        privacy_mode=PrivacyMode(doc["privacyMode"]),
        language=doc["language"],
        channel=Channel(doc["channel"]),
        agent=Agent.from_dict(doc["agent"]),
        turn=Turn.from_dict(doc["turn"]),
        recording=Recording.from_dict(doc["recording"]),
        budgets=Budgets.from_dict(doc["budgets"]),
        config_hash=doc.get("configHash"),
        allowed_regions=tuple(residency.get("allowedRegions", ())),
    )
    cfg.check()
    return cfg
