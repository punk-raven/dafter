from __future__ import annotations

from typing import Any

import pytest
from dafter_core.config import ProviderRef, Turn
from dafter_core.enums import ErrorCode, Stage, TurnStrategy
from dafter_core.errors import DafterError
from dafter_providers import VENDORS, credentials, sarvam, vendor_for
from dafter_providers.sarvam.realtime import FinalFirstSTT
from livekit.agents import APIConnectionError, APIStatusError, APITimeoutError

KEY_REF = "secret://tenants/t_9c21a4be/sarvam/api-key"
TURN = Turn(strategy=TurnStrategy.PROVIDER_ENDPOINTING, silence_ms=500, min_speech_ms=120)


@pytest.fixture(autouse=True)
def sarvam_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARVAM_API_KEY", "test-only-not-a-key")


def ref(model: str, **options: Any) -> ProviderRef:
    return ProviderRef(
        provider="sarvam", model=model, region="ap-south-1", credential_ref=KEY_REF, options=options
    )


def test_a_secret_ref_names_its_environment_variable() -> None:
    assert credentials.env_name(KEY_REF) == "SARVAM_API_KEY"
    assert credentials.env_name("secret://deepgram/api-key") == "DEEPGRAM_API_KEY"


def test_a_missing_credential_fails_at_construction_without_leaking() -> None:
    with pytest.raises(DafterError) as caught:
        credentials.resolve(KEY_REF, env={})
    assert caught.value.code is ErrorCode.AUTHENTICATION_FAILED
    assert "SARVAM_API_KEY" in caught.value.message


def test_stt_is_the_realtime_class_on_the_fast_profile() -> None:
    stt = sarvam.build_stt(ref("saaras:v3-realtime", chunkMs=500, sampleRate=16000), "hi", TURN)
    opts = stt._opts  # type: ignore[attr-defined]
    assert isinstance(stt, FinalFirstSTT)
    assert (opts.language, opts.stream_type, opts.endpointing) == ("hi-IN", "fast", "vad")
    assert (opts.encoding, opts.sample_rate) == ("linear16", 16000)
    assert (opts.vad_min_silence_ms, opts.vad_min_speech_ms) == (500, 120)


def test_llm_turns_thinking_off_on_the_generally_available_endpoint() -> None:
    llm = sarvam.build_llm(ref("sarvam-105b", thinking=False, maxTokens=200))
    assert llm._opts.reasoning_effort is None  # type: ignore[attr-defined]
    assert str(llm._client.base_url).rstrip("/") == sarvam.LLM_BASE_URL  # type: ignore[attr-defined]


def test_tts_speaks_raw_pcm_at_the_output_rate() -> None:
    tts = sarvam.build_tts(ref("bulbul:v3", sampleRate=24000, voice="shubh"), "hi")
    assert tts.sample_rate == 24000
    assert tts._opts.output_audio_codec == "linear16"  # type: ignore[attr-defined]
    assert tts._opts.target_language_code == "hi-IN"  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("build", "code", "pointer"),
    [
        (
            lambda: sarvam.build_stt(ref("saaras"), "hi", TURN),
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "/agent/pipeline/stt/model",
        ),
        (
            lambda: sarvam.build_stt(ref("saaras:v3-realtime", chunkMs=250), "hi", TURN),
            ErrorCode.INVALID_CONFIG,
            "/agent/pipeline/stt/options/chunkMs",
        ),
        (
            lambda: sarvam.build_tts(ref("bulbul:v3", codec="opus"), "hi"),
            ErrorCode.INVALID_CONFIG,
            "/agent/pipeline/tts/options/codec",
        ),
        (
            lambda: sarvam.build_llm(ref("sarvam-105b", thinking="no")),
            ErrorCode.INVALID_CONFIG,
            "/agent/pipeline/llm/options/thinking",
        ),
        (
            lambda: sarvam.build_tts(ref("bulbul:v3"), "fr-FR"),
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "/language",
        ),
    ],
)
def test_bad_settings_fail_at_construction_located_by_pointer(
    build: Any, code: ErrorCode, pointer: str
) -> None:
    with pytest.raises(DafterError) as caught:
        build()
    assert caught.value.code is code
    assert any(pointer in d for d in caught.value.details), caught.value.details


def test_a_speaker_the_model_lacks_is_refused_at_construction() -> None:
    with pytest.raises(DafterError) as caught:
        sarvam.build_tts(ref("bulbul:v3", voice="nobody"), "hi")
    assert caught.value.code is ErrorCode.INVALID_CONFIG
    assert caught.value.stage is Stage.TTS


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (APIStatusError("closed", status_code=1003), ErrorCode.AUTHENTICATION_FAILED, False),
        (APIStatusError("closed", status_code=1013), ErrorCode.PROVIDER_UNAVAILABLE, True),
        (APIStatusError("slow down", status_code=429), ErrorCode.RATE_LIMITED, True),
        (APITimeoutError(), ErrorCode.PROVIDER_TIMEOUT, True),
        (APIConnectionError(), ErrorCode.PROVIDER_UNAVAILABLE, True),
        (RuntimeError("bug"), ErrorCode.INTERNAL, False),
    ],
)
def test_vendor_failures_map_to_the_taxonomy(
    exc: BaseException, code: ErrorCode, retryable: bool
) -> None:
    err = sarvam.classify(exc, Stage.STT)
    assert (err.code, err.retryable) == (code, retryable)


def test_an_unregistered_provider_is_named_in_the_refusal() -> None:
    with pytest.raises(DafterError) as caught:
        vendor_for(ProviderRef(provider="deepgram"), Stage.STT)
    assert caught.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert set(VENDORS) == {"sarvam"}
