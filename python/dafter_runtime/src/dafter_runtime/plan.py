from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from dafter_core.config import Pipeline, ProviderRef, ResolvedSessionConfig, Turn, parse
from dafter_core.enums import AgentMode, EncryptionMode, ErrorCode, Stage, TurnStrategy
from dafter_core.errors import DafterError
from dafter_core.hashing import hash_document
from dafter_providers import Vendor, vendor_for

from .personas import Persona, persona_for

TurnDetection = Literal["stt", "manual"]


@dataclass(frozen=True, slots=True)
class Plan:
    config: ResolvedSessionConfig
    pipeline: Pipeline
    stt: Vendor
    llm: Vendor
    tts: Vendor
    turn_detection: TurnDetection
    turn_handling: dict[str, Any]
    persona: Persona


def load(metadata: str | bytes) -> ResolvedSessionConfig:
    cfg = parse(metadata)
    if cfg.config_hash is None or cfg.config_hash != hash_document(metadata):
        raise DafterError(
            ErrorCode.INVALID_CONFIG,
            "the job carries a document that does not match its own hash",
            details=("at '/configHash': recomputed over RFC 8785 and does not match",),
        )
    return cfg


def _refuse(code: ErrorCode, message: str, pointer: str, because: str) -> DafterError:
    return DafterError(code, message, details=(f"at '{pointer}': {because}",))


def _check_session(cfg: ResolvedSessionConfig, pool: str) -> Pipeline:
    if not cfg.agent.enabled:
        raise _refuse(
            ErrorCode.INVALID_CONFIG,
            "a job arrived for a session without an agent",
            "/agent/enabled",
            "false",
        )
    if cfg.agent.pool != pool:
        raise _refuse(
            ErrorCode.INVALID_CONFIG,
            "the job names another worker pool",
            "/agent/pool",
            f"this worker serves {pool}",
        )
    if cfg.agent.mode is not AgentMode.CASCADED:
        raise _refuse(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "this worker runs the cascaded pipeline only",
            "/agent/mode",
            "half_cascade and speech_to_speech need a realtime provider",
        )
    if cfg.media.encryption.stated_mode is EncryptionMode.E2EE:
        raise _refuse(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "this worker holds no end-to-end key, so it cannot hear an encrypted room",
            "/media/encryption/mode",
            "e2ee",
        )
    if cfg.agent.pipeline is None:
        raise _refuse(
            ErrorCode.INVALID_CONFIG,
            "the session names no pipeline",
            "/agent/pipeline",
            "a cascaded agent needs stt, llm and tts",
        )
    return cfg.agent.pipeline


def _vendor(ref: ProviderRef | None, stage: Stage, language: str) -> Vendor:
    vendor = vendor_for(ref, stage)
    if language not in vendor.languages:
        raise _refuse(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"the {stage} provider does not serve this session's language",
            "/language",
            f"not declared by {vendor.name}",
        )
    return vendor


def turn_detection(turn: Turn, stt: Vendor) -> TurnDetection:
    strategy = turn.strategy
    if strategy is TurnStrategy.AUTO:
        strategy = TurnStrategy.PROVIDER_ENDPOINTING if stt.native_endpointing else TurnStrategy.VAD
    if strategy is TurnStrategy.MANUAL:
        return "manual"
    if strategy is TurnStrategy.PROVIDER_ENDPOINTING and stt.native_endpointing:
        if turn.local_vad_enabled:
            raise _refuse(
                ErrorCode.INVALID_CONFIG,
                "provider endpointing with a local VAD runs two detectors on one stream",
                "/turn/localVadEnabled",
                "must be false when the recognizer's own VAD decides the turn",
            )
        return "stt"
    raise _refuse(
        ErrorCode.UNSUPPORTED_CAPABILITY,
        "this worker cannot run the session's turn strategy",
        "/turn/strategy",
        f"{strategy} needs a stage this worker does not register",
    )


def turn_handling(turn: Turn, detection: TurnDetection) -> dict[str, Any]:
    i = turn.interruption
    return {
        "turn_detection": detection,
        "endpointing": {"min_delay": turn.endpointing_delay_ms / 1000},
        "interruption": {
            "enabled": i.enabled,
            "min_duration": i.min_duration_ms / 1000,
            "min_words": i.min_words,
            "false_interruption_timeout": (
                i.false_interruption_timeout_ms / 1000 if i.false_interruption_timeout_ms else None
            ),
            "resume_false_interruption": i.resume_false_interruption,
        },
    }


def plan(cfg: ResolvedSessionConfig, pool: str) -> Plan:
    pipeline = _check_session(cfg, pool)
    stt = _vendor(pipeline.stt, Stage.STT, cfg.language)
    llm = _vendor(pipeline.llm, Stage.LLM, cfg.language)
    tts = _vendor(pipeline.tts, Stage.TTS, cfg.language)
    detection = turn_detection(cfg.turn, stt)
    return Plan(
        config=cfg,
        pipeline=pipeline,
        stt=stt,
        llm=llm,
        tts=tts,
        turn_detection=detection,
        turn_handling=turn_handling(cfg.turn, detection),
        persona=persona_for(cfg.agent.persona_ref, cfg.language),
    )
