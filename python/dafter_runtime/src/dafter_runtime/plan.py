from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from dafter_core.config import (
    DEFAULT_TURN_DETECTOR,
    Agent,
    Pipeline,
    ProviderRef,
    ResolvedSessionConfig,
    Turn,
    parse,
)
from dafter_core.enums import (
    AddressingMode,
    AgentMode,
    Channel,
    EncryptionMode,
    ErrorCode,
    NoiseCancellation,
    RecordingNotice,
    Stage,
    TurnStrategy,
)
from dafter_core.errors import DafterError
from dafter_core.hashing import hash_document
from dafter_core.rules import phone_guests
from dafter_providers import (
    TURN_DETECTORS,
    NoiseFilter,
    TurnDetectorKind,
    Vendor,
    noise_filter_for,
    turn_detector_for,
    vendor_for,
)
from dafter_providers.fallback import classified_first

from .personas import Persona, base_language, called_by_name, persona_for, recording_notice

TurnDetection = Literal["stt", "semantic", "manual"]
TURN_DETECTOR_LANGUAGES = TURN_DETECTORS[DEFAULT_TURN_DETECTOR].languages
TURN_DETECTOR_AT = "/turn/detector"
FALLBACK_AT = "/agent/pipeline/fallback"
NOISE_FILTER_AT = "/agent/pipeline/noiseFilter"
CLIENT_NOISE_MODELS = frozenset({NoiseCancellation.RNNOISE, NoiseCancellation.RNNOISE_GATED})


@dataclass(frozen=True, slots=True)
class Failover:
    vendor: Vendor
    ref: ProviderRef


@dataclass(frozen=True, slots=True)
class Plan:
    config: ResolvedSessionConfig
    pipeline: Pipeline
    stt: Vendor
    llm: Vendor
    tts: Vendor
    vad: Vendor | None
    turn_detection: TurnDetection
    turn_handling: dict[str, Any]
    persona: Persona
    stt_prompt: str | None = None
    disclosure: str | None = None
    personas: dict[str, Persona] = field(default_factory=dict)
    fallbacks: Mapping[Stage, tuple[Failover, ...]] = field(default_factory=dict)
    noise_filter: NoiseFilter | None = None
    turn_detector: TurnDetectorKind | None = None

    @property
    def hearing(self) -> str | None:
        return None if self.config.agent.language_switching.enabled else self.config.language

    @property
    def called_by_name(self) -> bool:
        return self.config.agent.addressing.waits_to_be_called

    @property
    def voice_turn_handling(self) -> dict[str, Any]:
        if self.called_by_name:
            return {**self.turn_handling, "turn_detection": "manual"}
        return self.turn_handling

    @property
    def opening(self) -> str | None:
        return self.persona.greeting if self.config.agent.greets else None

    @property
    def on_a_phone(self) -> bool:
        return self.config.channel is Channel.TELEPHONY

    @property
    def takes_phone_calls(self) -> bool:
        return self.on_a_phone or phone_guests(self.config)


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


def check_encryption(cfg: ResolvedSessionConfig, fetches_keys: bool) -> None:
    if cfg.media.encryption.stated_mode is not EncryptionMode.E2EE:
        return
    if not cfg.media.encryption.mints_shared_key:
        raise _refuse(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "this worker decrypts only under the control plane's shared session key",
            "/media/encryption/keyModel",
            "must be server_shared",
        )
    if not fetches_keys:
        raise _refuse(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "this worker has no control plane credential, so it cannot fetch the session key",
            "/media/encryption/mode",
            "e2ee needs DAFTER_CONTROL_URL and DAFTER_WORKER_SECRET on the worker",
        )


RUNS_ADDRESSING = frozenset({AddressingMode.ALWAYS, AddressingMode.TRANSCRIPT})


def _check_addressing(cfg: ResolvedSessionConfig) -> None:
    mode = cfg.agent.addressing.mode
    if mode not in RUNS_ADDRESSING:
        raise _refuse(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "this worker cannot wait to be called by name in this addressing mode",
            "/agent/addressing/mode",
            f"{mode} is not built in this worker; always and transcript are",
        )


def _check_session(cfg: ResolvedSessionConfig, pool: str, fetches_keys: bool) -> Pipeline:
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
    check_encryption(cfg, fetches_keys)
    _check_addressing(cfg)
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


def _same_route(ref: ProviderRef, primary: ProviderRef | None) -> bool:
    return primary is not None and (ref.provider, ref.model) == (primary.provider, primary.model)


def failovers(pipeline: Pipeline, language: str) -> dict[Stage, tuple[Failover, ...]]:
    chains = {
        Stage.LLM: (pipeline.llm, pipeline.fallback.llm),
        Stage.TTS: (pipeline.tts, pipeline.fallback.tts),
    }
    planned: dict[Stage, tuple[Failover, ...]] = {}
    for stage, (primary, staged) in chains.items():
        chain: list[Failover] = []
        for i, ref in enumerate(staged):
            if _same_route(ref, primary):
                continue
            at = f"{FALLBACK_AT}/{stage}/{i}"
            vendor = vendor_for(ref, stage, at)
            if language not in vendor.languages:
                raise _refuse(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    f"a {stage} fallback provider does not serve this session's language",
                    at,
                    f"not declared by {vendor.name}",
                )
            chain.append(Failover(vendor, ref))
        if chain:
            planned[stage] = tuple(chain)
    return planned


def local_vad(pipeline: Pipeline, turn: Turn, detection: TurnDetection) -> Vendor | None:
    if detection == "semantic":
        why = "the turn detector reads the local VAD's speech boundaries"
    elif turn.interruption.local_vad_enabled:
        why = "turn.interruption.localVadEnabled needs one"
    else:
        return None
    if pipeline.vad is None:
        raise _refuse(
            ErrorCode.INVALID_CONFIG,
            "the session needs a local VAD and the pipeline names none",
            "/agent/pipeline/vad",
            why,
        )
    return vendor_for(pipeline.vad, Stage.VAD)


def _semantic(turn: Turn, stt: Vendor, language: str) -> TurnDetection | None:
    detector = turn_detector_for(turn.detector)
    if base_language(language) not in detector.languages:
        return "stt" if stt.native_endpointing else None
    if not turn.local_vad_enabled:
        raise _refuse(
            ErrorCode.INVALID_CONFIG,
            "the turn detector decides the turn with a local VAD, and the session turns it off",
            "/turn/localVadEnabled",
            "must be true under the semantic strategy",
        )
    if because := detector.missing():
        raise _refuse(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "the session's turn detector cannot run in this worker",
            TURN_DETECTOR_AT,
            f"{detector.name}: {because}",
        )
    return "semantic"


def chosen_detector(turn: Turn, detection: TurnDetection) -> TurnDetectorKind | None:
    return turn_detector_for(turn.detector) if detection == "semantic" else None


def turn_detection(turn: Turn, stt: Vendor, language: str) -> TurnDetection:
    strategy = turn.strategy
    if strategy is TurnStrategy.AUTO:
        strategy = TurnStrategy.PROVIDER_ENDPOINTING if stt.native_endpointing else TurnStrategy.VAD
    if strategy is TurnStrategy.MANUAL:
        return "manual"
    if strategy is TurnStrategy.SEMANTIC and (detection := _semantic(turn, stt, language)):
        return detection
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


def endpointing(turn: Turn, detection: TurnDetection) -> dict[str, Any]:
    options: dict[str, Any] = {
        "mode": "dynamic" if detection == "semantic" else "fixed",
        "min_delay": turn.endpointing_delay_ms / 1000,
    }
    if turn.endpointing_max_delay_ms is not None:
        options["max_delay"] = turn.endpointing_max_delay_ms / 1000
    return options


def turn_handling(turn: Turn, detection: TurnDetection) -> dict[str, Any]:
    i = turn.interruption
    handling: dict[str, Any] = {} if detection == "semantic" else {"turn_detection": detection}
    pg = turn.preemptive_generation
    return handling | {
        "endpointing": endpointing(turn, detection),
        "preemptive_generation": {"enabled": pg.enabled, "preemptive_tts": pg.tts},
        "interruption": {
            "enabled": i.enabled,
            "mode": "vad",
            "min_duration": i.min_duration_ms / 1000,
            "min_words": i.min_words,
            "false_interruption_timeout": (
                i.false_interruption_timeout_ms / 1000 if i.false_interruption_timeout_ms else None
            ),
            "resume_false_interruption": i.resume_false_interruption,
        },
    }


def noise_filter(cfg: ResolvedSessionConfig, pipeline: Pipeline) -> NoiseFilter | None:
    chosen = noise_filter_for(pipeline.noise_filter)
    if chosen is None:
        return None
    if cfg.channel is Channel.TELEPHONY and not chosen.narrowband:
        raise _refuse(
            ErrorCode.INVALID_CONFIG,
            "the noise filter needs audio sampled above 8 kHz and a phone line carries 8 kHz",
            NOISE_FILTER_AT,
            f"{chosen.name} cannot run on the telephony channel; {chosen.phone_variant} can",
        )
    if cfg.media.audio.noise_cancellation in CLIENT_NOISE_MODELS:
        raise _refuse(
            ErrorCode.INVALID_CONFIG,
            "two noise models would run in series on the caller's audio",
            "/media/audio/noiseCancellation",
            "must be off or native while agent.pipeline.noiseFilter is not off",
        )
    if not chosen.installed():
        raise _refuse(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "the noise filter's plugin is not installed in this worker",
            NOISE_FILTER_AT,
            f"{chosen.vendor} {chosen.name} is not available",
        )
    return chosen


def disclosure(cfg: ResolvedSessionConfig) -> str | None:
    if cfg.channel is not Channel.TELEPHONY and not phone_guests(cfg):
        return None
    always = cfg.telephony.recording_notice is RecordingNotice.ALWAYS
    return recording_notice(cfg.language, cfg.recording.enabled, always)


def voiced(agent: Agent, language: str) -> Persona:
    persona = persona_for(agent.persona_ref, language, agent.name, agent.addressing.aliases)
    if not agent.addressing.waits_to_be_called:
        return persona
    return called_by_name(persona, agent.addressing.stays_awake)


def _switchable(cfg: ResolvedSessionConfig, vendors: tuple[Vendor, ...]) -> dict[str, Persona]:
    switching = cfg.agent.language_switching
    if not switching.enabled:
        return {}
    stt = vendors[0]
    if not stt.detects_language:
        raise _refuse(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "the session switches languages and its STT cannot identify one",
            "/agent/languageSwitching/enabled",
            f"{stt.name} does not identify the language of an utterance",
        )
    personas: dict[str, Persona] = {}
    bases: set[str] = set()
    for i, tag in enumerate(switching.languages):
        pointer = f"/agent/languageSwitching/languages/{i}"
        if base_language(tag) in bases:
            raise _refuse(
                ErrorCode.INVALID_CONFIG,
                "two languages the session may switch into share a base language",
                pointer,
                "an identified language could mean either",
            )
        bases.add(base_language(tag))
        for vendor in vendors:
            if tag not in vendor.languages:
                raise _refuse(
                    ErrorCode.UNSUPPORTED_CAPABILITY,
                    "a provider does not serve a language the session may switch into",
                    pointer,
                    f"not declared by {vendor.name}",
                )
        try:
            personas[tag] = voiced(cfg.agent, tag)
        except DafterError as exc:
            raise _refuse(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "no persona document is available for a language the session may switch into",
                pointer,
                "not registered in this worker for the language",
            ) from exc
    return personas


def _chained(vendor: Vendor) -> Vendor:
    return replace(vendor, classify=classified_first(vendor.classify))


def plan(cfg: ResolvedSessionConfig, pool: str, fetches_keys: bool = False) -> Plan:
    pipeline = _check_session(cfg, pool, fetches_keys)
    stt = _vendor(pipeline.stt, Stage.STT, cfg.language)
    llm = _chained(_vendor(pipeline.llm, Stage.LLM, cfg.language))
    tts = _chained(_vendor(pipeline.tts, Stage.TTS, cfg.language))
    detection = turn_detection(cfg.turn, stt, cfg.language)
    addressing = cfg.agent.addressing
    persona = voiced(cfg.agent, cfg.language)
    personas = _switchable(cfg, (stt, llm, tts)) or {cfg.language: persona}
    prompt = None
    if addressing.waits_to_be_called:
        prompt = ", ".join(dict.fromkeys((cfg.agent.name or "", *addressing.aliases)))
    return Plan(
        config=cfg,
        pipeline=pipeline,
        stt=stt,
        llm=llm,
        tts=tts,
        vad=local_vad(pipeline, cfg.turn, detection),
        turn_detection=detection,
        turn_handling=turn_handling(cfg.turn, detection),
        persona=persona,
        stt_prompt=prompt,
        disclosure=disclosure(cfg),
        personas=personas,
        fallbacks=failovers(pipeline, cfg.language),
        noise_filter=noise_filter(cfg, pipeline),
        turn_detector=chosen_detector(cfg.turn, detection),
    )
