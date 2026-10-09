from __future__ import annotations

import json
from pathlib import Path

import pytest
from dafter_core.speech import Backchannel
from dafter_runtime.lexicon import NO_CUES, Cue, Lexicon, phrases
from dafter_runtime.naming import words

CATALOG = Path(__file__).resolve().parents[3] / "go" / "cmd" / "dafter-control" / "catalog.json"
LANGUAGES = ("hi", "te", "kn", "mr", "en")
LATIN = frozenset("abcdefghijklmnopqrstuvwxyz -")


def catalog_backchannel() -> Backchannel:
    defaults = json.loads(CATALOG.read_text(encoding="utf-8"))["defaults"]
    return Backchannel.from_dict(defaults["turn"]["interruption"]["backchannel"])


CATALOG_LEXICON = Lexicon.of(catalog_backchannel())


def cue(text: str, lexicon: Lexicon = CATALOG_LEXICON) -> Cue:
    return lexicon.cue(words(text))


def test_every_focus_language_has_affirmatives_and_negatives_none_reviewed_yet() -> None:
    backchannel = catalog_backchannel()
    assert set(backchannel.words) == set(LANGUAGES)
    assert set(backchannel.negatives) == set(LANGUAGES)
    assert backchannel.reviewed == dict.fromkeys(LANGUAGES, False)
    for language in LANGUAGES:
        assert backchannel.words[language]
        assert backchannel.negatives[language]


@pytest.mark.parametrize("language", ["hi", "te", "kn", "mr"])
def test_each_indic_language_lists_its_native_script_and_the_romanized_form(language: str) -> None:
    backchannel = catalog_backchannel()
    for listed in (backchannel.words[language], backchannel.negatives[language]):
        assert any(set(text) <= LATIN for text in listed)
        assert any(not set(text) <= LATIN for text in listed)


@pytest.mark.parametrize("language", LANGUAGES)
def test_no_phrase_is_both_an_affirmative_and_a_negative(language: str) -> None:
    backchannel = catalog_backchannel()
    assert not phrases(backchannel.words[language]) & phrases(backchannel.negatives[language])


@pytest.mark.parametrize(
    "text",
    ["haan", "achha", "hmm", "ji", "sari", "avunu", "houdu", "ho", "barobar", "okay", "uh-huh"],
)
def test_a_standalone_affirmative_holds(text: str) -> None:
    assert cue(text) is Cue.AFFIRMATIVE
    assert not CATALOG_LEXICON.yields(words(text))


@pytest.mark.parametrize(
    "text",
    [
        "no no",
        "wait",
        "ruko",
        "nahi",
        "aagu",
        "illa",
        "thamba",
        "vaddu",
        "नहीं",
        "ಬೇಡ",
        "haan nahi",
    ],
)
def test_any_negative_yields(text: str) -> None:
    assert cue(text) is Cue.NEGATIVE
    assert CATALOG_LEXICON.yields(words(text))


def test_an_affirmative_followed_by_more_speech_yields() -> None:
    assert cue("haan ek baat") is Cue.CONTINUED
    assert cue("okay so what") is Cue.CONTINUED
    assert cue("ek baat haan") is Cue.SPEECH


def test_noise_and_plain_words_are_left_to_the_word_gate() -> None:
    assert cue("") is Cue.SILENT
    assert cue("uh um") is Cue.SILENT
    assert cue("ek do") is Cue.SPEECH


def test_affirmatives_from_elsewhere_extend_the_lexicon() -> None:
    def config(said: tuple[str, ...]) -> bool:
        return said == ("theek",)

    assert CATALOG_LEXICON.cue(words("theek"), config) is Cue.AFFIRMATIVE
    assert CATALOG_LEXICON.cue(words("theek then go"), config) is Cue.CONTINUED


def test_a_lexicon_hears_only_the_languages_its_config_lists() -> None:
    telugu = Lexicon.of(Backchannel(words={"te": ("sari",)}, negatives={"te": ("vaddu",)}))
    assert cue("vaddu", telugu) is Cue.NEGATIVE
    assert cue("thamba", telugu) is Cue.SPEECH
    assert cue("sari", telugu) is Cue.AFFIRMATIVE
    assert Lexicon.of(Backchannel()).negatives == frozenset()


def test_without_config_nothing_is_a_cue() -> None:
    assert cue("no no", NO_CUES) is Cue.SPEECH
    assert cue("haan", NO_CUES) is Cue.SPEECH
    assert not NO_CUES.yields(words("wait"))
