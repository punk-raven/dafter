from __future__ import annotations

import pytest
from dafter_runtime.personas import DEFAULT_REF, SCRIPTS, persona_for

NAME = "Nivya"
LANGUAGES = ("hi",)
REFS = (DEFAULT_REF, "persona://support/v3")
EVERY = [(ref, language) for ref in REFS for language in LANGUAGES]


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
