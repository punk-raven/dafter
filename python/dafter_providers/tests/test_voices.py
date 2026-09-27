from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from dafter_core.config import ProviderRef, parse
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError
from dafter_providers import Styled, sarvam
from dafter_providers.sarvam.sentences import SentenceTTS

KEY_REF = "secret://tenants/t_9c21a4be/sarvam/api-key"
STYLES = {"greeting": {"temperature": 0.8}, "concern": {"pace": 0.9, "temperature": 0.4}}


@pytest.fixture(autouse=True)
def sarvam_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARVAM_API_KEY", "test-only-not-a-key")


def tts(**options: Any) -> SentenceTTS:
    ref = ProviderRef(
        provider="sarvam",
        model="bulbul:v3",
        region="ap-south-1",
        credential_ref=KEY_REF,
        options=options,
    )
    built = sarvam.build_tts(ref, "hi")
    assert isinstance(built, SentenceTTS)
    return built


def settings(t: SentenceTTS) -> tuple[float, float]:
    return (t._opts.pace, t._opts.temperature)


def test_the_voice_takes_its_pace_and_temperature_from_the_options() -> None:
    assert settings(tts()) == (1.0, 0.6)
    assert settings(tts(pace=1.1, temperature=0.5)) == (1.1, 0.5)


def test_each_situation_switches_the_voice_and_neutral_or_unstyled_returns_to_it() -> None:
    t = tts(pace=1.1, temperature=0.5, styles=STYLES)
    assert isinstance(t, Styled)
    t.style("concern")
    assert settings(t) == (0.9, 0.4)
    t.style("greeting")
    assert settings(t) == (1.1, 0.8)
    t.style("neutral")
    assert settings(t) == (1.1, 0.5)


def test_a_pronunciation_dictionary_is_passed_by_id() -> None:
    assert tts()._opts.dict_id is None
    assert tts(dictionaryId="p_4a1f")._opts.dict_id == "p_4a1f"


@pytest.mark.parametrize(
    ("options", "pointer"),
    [
        ({"pace": 2.5}, "/agent/pipeline/tts/options/pace"),
        ({"temperature": 1.5}, "/agent/pipeline/tts/options/temperature"),
        ({"styles": {"angry": {"pace": 1.2}}}, "/agent/pipeline/tts/options/styles/angry"),
        ({"styles": {"concern": {"pitch": 0.2}}}, "/agent/pipeline/tts/options/styles/concern"),
        (
            {"styles": {"concern": {"pace": 0.3}}},
            "/agent/pipeline/tts/options/styles/concern/pace",
        ),
        (
            {"styles": {"greeting": {"temperature": "warm"}}},
            "/agent/pipeline/tts/options/styles/greeting/temperature",
        ),
        ({"dictionaryId": 7}, "/agent/pipeline/tts/options/dictionaryId"),
        ({"loudness": 1.2}, "/agent/pipeline/tts/options/loudness"),
    ],
)
def test_settings_bulbul_v3_does_not_take_are_refused_at_construction(
    options: dict[str, Any], pointer: str
) -> None:
    with pytest.raises(DafterError) as caught:
        tts(**options)
    assert caught.value.code is ErrorCode.INVALID_CONFIG
    assert any(pointer in d for d in caught.value.details), caught.value.details


def test_the_catalog_voice_is_calmer_for_a_concern_and_warmer_for_a_greeting() -> None:
    job = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
    ref = parse(job.read_bytes()).agent.pipeline
    assert ref is not None and ref.tts is not None
    built = sarvam.build_tts(ref.tts, "hi")
    assert isinstance(built, SentenceTTS)
    assert settings(built) == (1.0, 0.6)
    built.style("concern")
    assert settings(built) == (0.9, 0.4)
    built.style("greeting")
    assert settings(built) == (1.0, 0.8)


def test_sarvam_declares_no_markup_so_the_frameworks_expressive_mode_stays_off() -> None:
    built = tts()
    assert built.capabilities.streaming
    assert built.markup.llm_instructions() is None
