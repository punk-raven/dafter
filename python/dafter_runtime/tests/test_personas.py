from __future__ import annotations

import pytest
from dafter_runtime.personas import DEFAULT_REF, FEMININE, SCRIPTS, persona_for, written_in

NAME = "Nivya"
ALIASES = ("निव्या", "ನಿವ್ಯ", "ನಿವ್ಯಾ", "నివ్య", "నివ్యా")
SPOKEN = {"hi": "निव्या", "mr": "निव्या", "kn": "ನಿವ್ಯ", "te": "నివ్య", "en": "Nivya"}
LANGUAGES = ("hi", "en", "kn", "mr", "te")
REFS = (DEFAULT_REF, "persona://support/v3")
EVERY = [(ref, language) for ref in REFS for language in LANGUAGES]
MASCULINE = {
    "hi": ("सकता", "रहा हूँ", "देखता", "बताता"),
    "mr": ("शकतो", "बोलतोय", "बघतो"),
}
FEMININE_FORMS = {"hi": ("सकती", "रही"), "mr": ("शकते", "बोलतेय")}
GENDERED = [(ref, language) for ref, language in EVERY if language in MASCULINE]


def test_every_persona_is_scripted_for_every_language_the_worker_speaks() -> None:
    assert set(SCRIPTS) == set(EVERY)


@pytest.mark.parametrize(("ref", "language"), EVERY)
def test_the_persona_and_greeting_carry_the_configured_name(ref: str, language: str) -> None:
    persona = persona_for(ref, language, NAME)
    assert persona.instructions.startswith(f"You are {NAME}, ")
    assert NAME in persona.greeting
    assert "dafter" not in (persona.instructions + persona.greeting).casefold()


@pytest.mark.parametrize(("ref", "language"), EVERY)
def test_an_unnamed_agent_introduces_itself_by_no_name(ref: str, language: str) -> None:
    persona = persona_for(ref, language, None)
    assert persona.instructions.startswith("You are a ")
    assert persona.greeting == SCRIPTS[(ref, language)].greeting
    assert "dafter" not in (persona.instructions + persona.greeting).casefold()


@pytest.mark.parametrize(("ref", "language"), EVERY)
def test_the_greeting_says_the_name_in_its_own_script(ref: str, language: str) -> None:
    persona = persona_for(ref, language, NAME, ALIASES)
    assert persona.instructions.startswith(f"You are {NAME}, ")
    assert SPOKEN[language] in persona.greeting
    assert written_in(persona.greeting, language), persona.greeting


def test_a_language_without_a_spelling_of_its_own_says_the_configured_name() -> None:
    assert "Nivya" in persona_for(DEFAULT_REF, "kn", NAME, ("निव्या",)).greeting


@pytest.mark.parametrize(("ref", "language"), EVERY)
@pytest.mark.parametrize("name", [NAME, None])
def test_the_agent_is_told_she_is_a_woman(ref: str, language: str, name: str | None) -> None:
    assert FEMININE in persona_for(ref, language, name).instructions


@pytest.mark.parametrize(("ref", "language"), GENDERED)
def test_the_agent_greets_in_the_feminine(ref: str, language: str) -> None:
    script = SCRIPTS[(ref, language)]
    for line in (script.greeting, script.introduction):
        assert not any(form in line for form in MASCULINE[language]), line
        assert any(form in line for form in FEMININE_FORMS[language]), line
