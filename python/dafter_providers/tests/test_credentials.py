from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from dafter_core.config import ProviderRef, Turn
from dafter_core.enums import ErrorCode, Stage, TurnStrategy
from dafter_core.errors import DafterError
from dafter_providers import VENDORS, credentials, sarvam

PLATFORM_VALUE = "platform-secret-value"
PLATFORM_REFS = {
    "DAFTER_LIVEKIT_API_SECRET": "secret://tenants/t_9c21a4be/dafter/livekit-api-secret",
    "DAFTER_WORKER_SECRET": "secret://tenants/t_9c21a4be/dafter/worker-secret",
    "DAFTER_EGRESS_S3_SECRET": "secret://tenants/t_9c21a4be/dafter/egress-s3-secret",
    "LIVEKIT_API_SECRET": "secret://livekit/api-secret",
}
TURN = Turn(strategy=TurnStrategy.PROVIDER_ENDPOINTING, silence_ms=500, min_speech_ms=120)
Build = Callable[[ProviderRef], Any]
STAGES: dict[str, tuple[Stage, str, dict[str, Any], Build]] = {
    "sarvam stt": (
        Stage.STT,
        "saaras:v3-realtime",
        {},
        lambda r: sarvam.build_stt(r, "hi", TURN, None),
    ),
    "sarvam llm": (Stage.LLM, "sarvam-105b", {}, sarvam.build_llm),
    "sarvam tts": (Stage.TTS, "bulbul:v3", {}, lambda r: sarvam.build_tts(r, "hi")),
    "groq llm": (Stage.LLM, "a-model", {}, lambda r: llm_of("groq")(r)),
    "openrouter llm": (Stage.LLM, "a-model", {}, lambda r: llm_of("openrouter")(r)),
    "google llm": (Stage.LLM, "a-model", {}, lambda r: llm_of("google")(r)),
}


@pytest.fixture(autouse=True)
def platform_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in PLATFORM_REFS:
        monkeypatch.setenv(name, PLATFORM_VALUE)
    for name in ("SARVAM_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(name, "test-only-not-a-key")


def llm_of(vendor: str) -> Build:
    build = VENDORS[vendor].llm
    assert build is not None
    return build


def stage_ref(vendor_stage: str, credential_ref: str) -> ProviderRef:
    _, model, options, _ = STAGES[vendor_stage]
    region = "ap-south-1" if vendor_stage.startswith("sarvam") else None
    return ProviderRef(
        provider=vendor_stage.split()[0],
        model=model,
        region=region,
        credential_ref=credential_ref,
        options=options,
    )


def refused(vendor_stage: str, credential_ref: str) -> DafterError:
    stage, _, _, build = STAGES[vendor_stage]
    with pytest.raises(DafterError) as caught:
        build(stage_ref(vendor_stage, credential_ref))
    err = caught.value
    assert err.code is ErrorCode.INVALID_CONFIG, err
    pointer = f"/agent/pipeline/{stage}/credentialRef"
    assert any(pointer in d for d in err.details), err.details
    assert PLATFORM_VALUE not in f"{err.message} {err.details}"
    return err


@pytest.mark.parametrize("vendor_stage", STAGES)
@pytest.mark.parametrize("platform_ref", PLATFORM_REFS.values())
def test_no_stage_resolves_a_platform_secret_whatever_the_ref_says(
    vendor_stage: str, platform_ref: str
) -> None:
    refused(vendor_stage, platform_ref)


@pytest.mark.parametrize(
    ("vendor_stage", "other"),
    [
        ("sarvam stt", "groq"),
        ("sarvam llm", "openrouter"),
        ("sarvam tts", "groq"),
        ("groq llm", "openrouter"),
        ("groq llm", "sarvam"),
        ("openrouter llm", "groq"),
        ("google llm", "openrouter"),
        ("sarvam llm", "gemini"),
    ],
)
def test_a_vendor_never_receives_another_vendors_key(vendor_stage: str, other: str) -> None:
    refused(vendor_stage, f"secret://tenants/t_9c21a4be/{other}/api-key")


def test_the_allow_list_holds_provider_keys_only() -> None:
    assert credentials.PROVIDER_CREDENTIALS
    for name in credentials.PROVIDER_CREDENTIALS:
        assert name.endswith("_API_KEY"), name
        assert not name.startswith(("DAFTER_", "LIVEKIT_")), name


def test_a_vendor_that_names_a_platform_secret_still_cannot_read_it() -> None:
    env = {"SARVAM_API_KEY": "k", "DAFTER_WORKER_SECRET": PLATFORM_VALUE}
    ref = stage_ref("sarvam llm", "secret://tenants/t_9c21a4be/sarvam/api-key")
    assert credentials.resolve(ref, Stage.LLM, {"SARVAM_API_KEY"}, env) == "k"
    ref = stage_ref("sarvam llm", PLATFORM_REFS["DAFTER_WORKER_SECRET"])
    with pytest.raises(DafterError) as caught:
        credentials.resolve(ref, Stage.LLM, {"DAFTER_WORKER_SECRET"}, env)
    assert caught.value.code is ErrorCode.INVALID_CONFIG
    assert PLATFORM_VALUE not in f"{caught.value.message} {caught.value.details}"
