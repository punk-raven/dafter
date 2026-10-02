from __future__ import annotations

from typing import Any

import pytest
from dafter_core.config import ProviderRef
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError
from dafter_providers import silero, vendor_for
from livekit.agents import inference


def ref(**options: Any) -> ProviderRef:
    return ProviderRef(provider="silero", options=options)


def test_the_vad_is_the_framework_bundled_silero_model() -> None:
    vad = vendor_for(ref(), Stage.VAD).vad
    assert vad is not None
    built = vad(ref())
    assert isinstance(built, inference.VAD)
    assert (built.model, built.provider) == ("silero", "livekit-local-inference")


def test_options_are_milliseconds_in_config_and_seconds_in_the_framework() -> None:
    built = silero.build_vad(
        ref(minSpeechMs=80, minSilenceMs=300, prefixPaddingMs=200, activationThreshold=0.6)
    )
    opts = built._opts  # type: ignore[attr-defined]
    assert (opts.min_speech_duration, opts.min_silence_duration) == (0.08, 0.3)
    assert (opts.prefix_padding_duration, opts.activation_threshold) == (0.2, 0.6)


@pytest.mark.parametrize(
    ("bad", "code", "pointer"),
    [
        (
            ref(minSilenceMs=-1),
            ErrorCode.INVALID_CONFIG,
            "/agent/pipeline/vad/options/minSilenceMs",
        ),
        (
            ref(activationThreshold=1),
            ErrorCode.INVALID_CONFIG,
            "/agent/pipeline/vad/options/activationThreshold",
        ),
        (ref(onsetMs=10), ErrorCode.INVALID_CONFIG, "/agent/pipeline/vad/options/onsetMs"),
        (
            ProviderRef(provider="silero", model="silero-v4"),
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "/agent/pipeline/vad/model",
        ),
    ],
)
def test_bad_settings_fail_at_construction_located_by_pointer(
    bad: ProviderRef, code: ErrorCode, pointer: str
) -> None:
    with pytest.raises(DafterError) as caught:
        silero.build_vad(bad)
    assert caught.value.code is code
    assert caught.value.stage is Stage.VAD
    assert any(pointer in d for d in caught.value.details), caught.value.details


def test_a_vad_failure_is_an_internal_error_of_the_vad_stage() -> None:
    err = silero.classify(RuntimeError("bug"), Stage.VAD)
    assert (err.code, err.stage, err.retryable) == (ErrorCode.INTERNAL, Stage.VAD, False)
