from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from dafter_core.errors import DafterError
from dafter_evals.script import HINDI, Script
from dafter_evals.scripts import LANGUAGES, SCRIPTS, caller_for, script_for
from dafter_evals.voice import CALLER_VOICES, caller_voice
from livekit.plugins.sarvam.tts import MODEL_SPEAKER_COMPATIBILITY

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "go" / "cmd" / "dafter-control" / "catalog.json"
NATIVE_LETTERS = {
    "hi": range(0x0900, 0x0980),
    "mr-IN": range(0x0900, 0x0980),
    "te-IN": range(0x0C00, 0x0C80),
    "kn-IN": range(0x0C80, 0x0D00),
}


def catalog() -> dict[str, Any]:
    parsed: dict[str, Any] = json.loads(CATALOG.read_text(encoding="utf-8"))
    return parsed


def base_language(language: str) -> str:
    return language.split("-")[0]


def test_every_catalog_language_has_a_caller_script_and_voice() -> None:
    assert set(LANGUAGES) == set(catalog()["languages"])
    assert set(CALLER_VOICES) == set(LANGUAGES)


@pytest.mark.parametrize("language", LANGUAGES)
def test_each_script_carries_every_scenario(language: str) -> None:
    script = script_for(language)
    assert len(script.turns) == len(HINDI.turns)
    assert len(script.paused) == len(HINDI.paused)
    assert all(first and second for first, second in script.paused)
    overlaps = (script.interruptions, script.backchannels, script.fillers)
    assert all(len(texts) == len(script.long_prompts) for texts in overlaps)


@pytest.mark.parametrize("language", sorted(NATIVE_LETTERS))
def test_indic_scripts_are_written_in_native_script(language: str) -> None:
    letters = NATIVE_LETTERS[language]
    script = script_for(language)
    spoken = script.turns + script.long_prompts + script.interruptions + script.backchannels
    for text in spoken:
        assert any(ord(ch) in letters for ch in text), text


@pytest.mark.parametrize("language", LANGUAGES)
def test_backchannels_are_words_the_agent_treats_as_acknowledgements(language: str) -> None:
    words = catalog()["defaults"]["turn"]["interruption"]["backchannel"]["words"]
    listed = {w.lower() for w in words[base_language(language)]}
    assert {w.lower() for w in script_for(language).backchannels} <= listed


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_caller_voice_is_a_bulbul_speaker_unlike_the_agent(language: str) -> None:
    overlay = catalog()["languages"][language]["overlay"]
    agent_voice = overlay["agent"]["pipeline"]["tts"]["options"]["voice"]
    voice = caller_voice(language)
    assert voice in MODEL_SPEAKER_COMPATIBILITY["bulbul:v3"]["all"]
    assert voice != agent_voice


def test_only_the_hindi_script_is_reviewed_by_a_native_speaker() -> None:
    assert [lang for lang, s in SCRIPTS.items() if s.reviewed] == ["hi"]


def test_the_caller_follows_the_language_and_takes_a_given_speaker() -> None:
    telugu = caller_for("te-IN")
    assert (telugu.language, telugu.speaker) == ("te-IN", CALLER_VOICES["te-IN"])
    assert isinstance(telugu.script, Script) and telugu.script is SCRIPTS["te-IN"]
    assert caller_for("hi", "shubh").speaker == "shubh"
    assert caller_for("hi").script is HINDI


def test_an_unknown_language_is_refused() -> None:
    with pytest.raises(ValueError, match="ta-IN"):
        script_for("ta-IN")
    with pytest.raises(DafterError):
        caller_voice("ta-IN")
