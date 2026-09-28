from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from dafter_core.config import ProviderRef, Turn
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError, ProviderContext
from livekit.agents import APIConnectionError, APIStatusError, APITimeoutError, llm, stt, tts

from .. import credentials
from ..options import Options
from .batch import build_batch as build_batch
from .languages import LANGUAGES as LANGUAGES
from .languages import language_code
from .llm import SarvamLLM
from .realtime import FinalFirstSTT
from .sentences import SentenceTTS

NAME = "sarvam"
STT_MODELS = frozenset({"saaras:v3-realtime"})
LLM_MODELS = frozenset({"sarvam-105b", "sarvam-105b-conversations"})
TTS_MODELS = frozenset({"bulbul:v3"})
REGIONS = frozenset({"ap-south-1"})
LLM_BASE_URL = "https://api.sarvam.ai/v1"
AGENT_LLM = "/agent/pipeline/llm"
CREDENTIAL = "SARVAM_API_KEY"

CHUNK_PROFILES = {500: "fast", 1000: "balanced"}
STT_ENCODINGS = {"pcm_s16le": "linear16", "mulaw": "mulaw"}
STT_SAMPLE_RATES = {8000: 8000, 16000: 16000}
TTS_ENCODINGS = {"pcm_s16le": "linear16", "mulaw": "mulaw"}
TTS_SAMPLE_RATES = {r: r for r in (8000, 16000, 22050, 24000, 48000)}
MAX_FINAL_GRACE_MS = 10000
MIN_BUFFER_CHARS = (30, 200)

T = TypeVar("T")


def _checked(ref: ProviderRef, stage: Stage, models: frozenset[str], at: str | None = None) -> None:
    context = ProviderContext(NAME)
    base = at or f"/agent/pipeline/{stage}"
    if ref.model not in models:
        raise DafterError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"{NAME} {stage} is pinned to a model this worker does not run",
            stage=stage,
            provider=context,
            details=(f"at '{base}/model': one of {', '.join(sorted(models))}",),
        )
    if ref.region is not None and ref.region not in REGIONS:
        raise DafterError(
            ErrorCode.RESIDENCY_VIOLATION,
            f"{NAME} does not serve the region this stage is pinned to",
            stage=stage,
            provider=context,
        )


def _construct(stage: Stage, build: Callable[[], T]) -> T:
    try:
        return build()
    except ValueError as exc:
        raise DafterError(
            ErrorCode.INVALID_CONFIG,
            f"{NAME} refused the {stage} settings at construction: {exc}",
            stage=stage,
            provider=ProviderContext(NAME),
        ) from exc


def build_stt(ref: ProviderRef, language: str, turn: Turn, prompt: str | None) -> stt.STT[Any]:
    _checked(ref, Stage.STT, STT_MODELS)
    opts = Options(
        Stage.STT, NAME, ref.options, ("chunkMs", "encoding", "sampleRate", "finalGraceMs")
    )
    code = language_code(language, Stage.STT)
    stream_type = opts.choice("chunkMs", CHUNK_PROFILES, 500)
    encoding = opts.choice("encoding", STT_ENCODINGS, "pcm_s16le")
    sample_rate = opts.choice("sampleRate", STT_SAMPLE_RATES, 16000)
    final_grace_ms = opts.get("finalGraceMs", int, 1500)
    if not 0 <= final_grace_ms <= MAX_FINAL_GRACE_MS:
        raise opts.error(
            "an option is out of range",
            f"at '{opts.pointer('finalGraceMs')}': between 0 and {MAX_FINAL_GRACE_MS}",
        )
    key = credentials.resolve(ref, Stage.STT, {CREDENTIAL})
    return _construct(
        Stage.STT,
        lambda: FinalFirstSTT(
            language=code,
            stream_type=stream_type,
            endpointing="vad",
            encoding=encoding,
            sample_rate=sample_rate,
            api_key=key,
            vad_min_speech_ms=turn.min_speech_ms or None,
            vad_min_silence_ms=turn.silence_ms or None,
            final_grace=final_grace_ms / 1000,
            prompt=prompt,
        ),
    )


def build_llm(ref: ProviderRef, at: str = AGENT_LLM) -> llm.LLM[Any]:
    _checked(ref, Stage.LLM, LLM_MODELS, at)
    opts = Options(
        Stage.LLM, NAME, ref.options, ("prewarm", "thinking", "temperature", "maxTokens"), base=at
    )
    opts.get("prewarm", bool, True)
    thinking = opts.get("thinking", bool, False)
    temperature = opts.get("temperature", float, 0.4)
    max_tokens = opts.get("maxTokens", int, 200)
    key = credentials.resolve(ref, Stage.LLM, {CREDENTIAL}, pointer=at)
    model = ref.model or ""
    if thinking:
        return _construct(
            Stage.LLM,
            lambda: SarvamLLM(
                model=model,
                api_key=key,
                base_url=LLM_BASE_URL,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )
    return _construct(
        Stage.LLM,
        lambda: SarvamLLM(
            model=model,
            api_key=key,
            base_url=LLM_BASE_URL,
            temperature=temperature,
            max_tokens=max_tokens,
            reasoning_effort=None,
        ),
    )


def build_tts(ref: ProviderRef, language: str) -> tts.TTS[Any]:
    _checked(ref, Stage.TTS, TTS_MODELS)
    opts = Options(
        Stage.TTS,
        NAME,
        ref.options,
        ("prewarm", "encoding", "sampleRate", "voice", "pace", "minBufferSize"),
    )
    code = language_code(language, Stage.TTS)
    min_buffer = opts.get("minBufferSize", int, 50)
    if not MIN_BUFFER_CHARS[0] <= min_buffer <= MIN_BUFFER_CHARS[1]:
        raise opts.error(
            "an option is out of range",
            f"at '{opts.pointer('minBufferSize')}': between {MIN_BUFFER_CHARS[0]} and "
            f"{MIN_BUFFER_CHARS[1]} characters",
        )
    encoding = opts.choice("encoding", TTS_ENCODINGS, "pcm_s16le")
    sample_rate = opts.choice("sampleRate", TTS_SAMPLE_RATES, 24000)
    voice = opts.get("voice", str, "shubh")
    pace = opts.get("pace", float, 1.0)
    opts.get("prewarm", bool, True)
    key = credentials.resolve(ref, Stage.TTS, {CREDENTIAL})
    model = ref.model or ""
    return _construct(
        Stage.TTS,
        lambda: SentenceTTS(
            target_language_code=code,
            model=model,
            speaker=voice,
            speech_sample_rate=sample_rate,
            pace=pace,
            min_buffer_size=min_buffer,
            api_key=key,
            output_audio_codec=encoding,
        ),
    )


def wants_prewarm(ref: ProviderRef) -> bool:
    return ref.options.get("prewarm", True) is True


def classify(exc: BaseException, stage: Stage) -> DafterError:
    native: str | None = None
    code = ErrorCode.PROVIDER_UNAVAILABLE
    if isinstance(exc, APITimeoutError):
        code = ErrorCode.PROVIDER_TIMEOUT
    elif isinstance(exc, APIStatusError):
        native = str(exc.status_code)
        if exc.status_code in (401, 403, 1003, 1008):
            code = ErrorCode.AUTHENTICATION_FAILED
        elif exc.status_code == 429:
            code = ErrorCode.RATE_LIMITED
        elif exc.status_code == 400:
            code = ErrorCode.INVALID_CONFIG
    elif not isinstance(exc, APIConnectionError):
        code = ErrorCode.INTERNAL
    return DafterError(
        code,
        f"{NAME} {stage} failed ({type(exc).__name__})",
        stage=stage,
        provider=ProviderContext(NAME, native_code=native),
    )
