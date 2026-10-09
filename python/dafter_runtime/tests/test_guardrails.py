from __future__ import annotations

import pytest
from dafter_runtime.guardrails import SAFE_LINES, guardrail_rules, unreviewed_languages
from dafter_runtime.personas import written_in

LANGUAGES = ("hi", "en", "kn", "mr", "te")
MASCULINE = {"hi": ("सकता", "रहा हूँ", "करता"), "mr": ("शकतो", "बोलतोय", "करतो")}
INFORMAL = {"hi": ("तुम", "तू "), "kn": ("ನೀನು",), "mr": ("तू ",), "te": ("నువ్వు",)}


def test_every_language_the_worker_speaks_has_safe_lines() -> None:
    assert set(SAFE_LINES) == set(LANGUAGES)


def test_only_the_english_wording_counts_as_reviewed() -> None:
    assert unreviewed_languages() == ("hi", "kn", "mr", "te")


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_rules_cover_every_guarded_topic(language: str) -> None:
    rules = guardrail_rules(language)
    for topic in (
        "harmful, dangerous or illegal",
        "diagnose, name a medicine or dose",
        "legal case",
        "what to invest in",
        "hurting or killing themselves",
        "OTP, PIN, password",
        "ignore or change these rules",
        "reveal your instructions",
        "Stay within your role",
    ):
        assert topic in rules, topic


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_rules_carry_the_spoken_lines_of_their_language(language: str) -> None:
    lines = SAFE_LINES[language]
    rules = guardrail_rules(language)
    assert all(line in rules for line in (lines.decline, lines.referral, lines.crisis))


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_crisis_line_names_a_helpline_and_the_emergency_number(language: str) -> None:
    crisis = SAFE_LINES[language].crisis
    assert "Tele MANAS" in crisis
    if language == "hi":
        assert "14416" in crisis and "112" in crisis
    else:
        assert "one four four one six" in crisis and "one one two" in crisis
        assert not any(c.isdigit() for c in crisis)


@pytest.mark.parametrize("language", [lang for lang in LANGUAGES if lang != "en"])
def test_indic_lines_are_written_in_their_own_script(language: str) -> None:
    lines = SAFE_LINES[language]
    for line in (lines.decline, lines.referral, lines.crisis):
        native = "".join(c for c in line if not c.isascii())
        assert written_in(native, language), line


@pytest.mark.parametrize("language", sorted(MASCULINE))
def test_the_lines_keep_her_feminine_grammar(language: str) -> None:
    lines = SAFE_LINES[language]
    for line in (lines.decline, lines.referral, lines.crisis):
        assert not any(form in line for form in MASCULINE[language]), line


@pytest.mark.parametrize("language", sorted(INFORMAL))
def test_the_lines_address_the_caller_politely(language: str) -> None:
    lines = SAFE_LINES[language]
    for line in (lines.decline, lines.referral, lines.crisis):
        assert not any(form in line for form in INFORMAL[language]), line


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_rules_stay_short_and_use_no_em_dash(language: str) -> None:
    rules = guardrail_rules(language)
    assert len(rules.split()) < 220
    assert chr(0x2014) not in rules


def test_an_unknown_language_has_no_guardrails() -> None:
    with pytest.raises(KeyError):
        guardrail_rules("ta")
