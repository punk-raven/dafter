from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from dafter_core.config import ProviderRef, Turn
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError, ProviderContext
from livekit.agents import APIConnectionError, APIStatusError, APITimeoutError, llm, stt, tts
from livekit.plugins import sarvam as plugin

from .. import credentials
from ..options import Options
from .realtime import FinalFirstSTT

NAME = "sarvam"
STT_MODELS = frozenset({"saaras:v3-realtime"})
LLM_MODELS = frozenset({"sarvam-105b", "sarvam-105b-conversations"})
TTS_MODELS = frozenset({"bulbul:v3"})
REGIONS = frozenset({"ap-south-1"})
LLM_BASE_URL = "https://api.sarvam.ai/v1"

LANGUAGES = {
    "hi": "hi-IN",
    "hi-IN": "hi-IN",
    "en-IN": "en-IN",
    "bn-IN": "bn-IN",
    "kn-IN": "kn-IN",
    "ml-IN": "ml-IN",
    "mr-IN": "mr-IN",
    "ta-IN": "ta-IN",
    "te-IN": "te-IN",
    "gu-IN": "gu-IN",
    "pa-IN": "pa-IN",
    "or-IN": "or-IN",
}
CHUNK_PROFILES = {500: "fast", 1000: "balanced"}
STT_ENCODINGS = {"pcm_s16le": "linear16", "mulaw": "mulaw"}
STT_SAMPLE_RATES = {8000: 8000, 16000: 16000}
TTS_ENCODINGS = {"pcm_s16le": "linear16", "mulaw": "mulaw"}
TTS_SAMPLE_RATES = {r: r for r in (8000, 16000, 22050, 24000, 48000)}

T = TypeVar("T")


def _checked(ref: ProviderRef, stage: Stage, models: frozenset[str]) -> None:
    context = ProviderContext(NAME)
    if ref.model not in models:
        raise DafterError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"{NAME} {stage} is pinned to a model this worker does not run",
            stage=stage,
            provider=context,
            details=(f"at '/agent/pipeline/{stage}/model': one of {', '.join(sorted(models))}",),
        )
    if ref.region is not None and ref.region not in REGIONS:
        raise DafterError(
            ErrorCode.RESIDENCY_VIOLATION,
            f"{NAME} does not serve the region this stage is pinned to",
            stage=stage,
            provider=context,
        )


def language_code(tag: str, stage: Stage) -> str:
    code = LANGUAGES.get(tag)
    if code is None:
        raise DafterError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"{NAME} {stage} does not serve this session's language",
            stage=stage,
            provider=ProviderContext(NAME),
            details=("at '/language': not a language this provider declares",),
        )
    return code


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


def build_stt(ref: ProviderRef, language: str, turn: Turn) -> stt.STT[Any]:
    _checked(ref, Stage.STT, STT_MODELS)
    opts = Options(Stage.STT, NAME, ref.options, ("chunkMs", "encoding", "sampleRate"))
    code = language_code(language, Stage.STT)
    stream_type = opts.choice("chunkMs", CHUNK_PROFILES, 500)
    encoding = opts.choice("encoding", STT_ENCODINGS, "pcm_s16le")
    sample_rate = opts.choice("sampleRate", STT_SAMPLE_RATES, 16000)
    key = credentials.resolve(ref.credential_ref)
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
        ),
    )


def build_llm(ref: ProviderRef) -> llm.LLM[Any]:
    _checked(ref, Stage.LLM, LLM_MODELS)
    opts = Options(Stage.LLM, NAME, ref.options, ("thinking", "temperature", "maxTokens"))
    thinking = opts.get("thinking", bool, False)
    temperature = opts.get("temperature", float, 0.4)
    max_tokens = opts.get("maxTokens", int, 200)
    key = credentials.resolve(ref.credential_ref)
    model = ref.model or ""
    if thinking:
        return _construct(
            Stage.LLM,
            lambda: plugin.LLM(
                model=model,
                api_key=key,
                base_url=LLM_BASE_URL,
                temperature=temperature,
                max_tokens=max_tokens,
            ),
        )
    return _construct(
        Stage.LLM,
        lambda: plugin.LLM(
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
        Stage.TTS, NAME, ref.options, ("prewarm", "encoding", "sampleRate", "voice", "pace")
    )
    code = language_code(language, Stage.TTS)
    encoding = opts.choice("encoding", TTS_ENCODINGS, "pcm_s16le")
    sample_rate = opts.choice("sampleRate", TTS_SAMPLE_RATES, 24000)
    voice = opts.get("voice", str, "shubh")
    pace = opts.get("pace", float, 1.0)
    opts.get("prewarm", bool, True)
    key = credentials.resolve(ref.credential_ref)
    model = ref.model or ""
    return _construct(
        Stage.TTS,
        lambda: plugin.TTS(
            target_language_code=code,
            model=model,
            speaker=voice,
            speech_sample_rate=sample_rate,
            pace=pace,
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
