from __future__ import annotations

from dataclasses import dataclass

from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError

DEFAULT_REF = "persona://default"


@dataclass(frozen=True, slots=True)
class Persona:
    instructions: str
    greeting: str


_HINDI_VOICE_RULES = (
    "You are speaking on a live voice call. Reply only in Hindi, written in Devanagari script. "
    "Keep every reply to one or two short spoken sentences. Never use markdown, lists, "
    "headings, emojis or symbols that cannot be spoken aloud. Write numbers as words. "
    "If the caller only says something like hmm or okay, reply with a very short acknowledgement."
)

PERSONAS: dict[tuple[str, str], Persona] = {
    (DEFAULT_REF, "hi"): Persona(
        instructions="You are Dafter, a friendly general assistant. " + _HINDI_VOICE_RULES,
        greeting="नमस्ते! मैं आपकी क्या मदद कर सकता हूँ?",
    ),
    ("persona://support/v3", "hi"): Persona(
        instructions=(
            "You are Dafter, a patient customer support agent who helps callers "
            "describe and solve their problem step by step. " + _HINDI_VOICE_RULES
        ),
        greeting="नमस्ते! मैं सहायता टीम से बात कर रहा हूँ। बताइए, क्या समस्या है?",
    ),
}


def base_language(tag: str) -> str:
    return tag.split("-", 1)[0].lower()


def persona_for(ref: str | None, language: str) -> Persona:
    key = (ref or DEFAULT_REF, base_language(language))
    persona = PERSONAS.get(key)
    if persona is None:
        raise DafterError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "no persona document is available for this reference and language",
            details=("at '/agent/personaRef': not registered in this worker for the language",),
        )
    return persona
