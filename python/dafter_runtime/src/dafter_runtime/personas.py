from __future__ import annotations

from dataclasses import dataclass

from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError

DEFAULT_REF = "persona://default"


@dataclass(frozen=True, slots=True)
class Persona:
    instructions: str
    greeting: str


_VOICE_RULES = (
    "Open every reply with one short sentence of five to eight words, so it can be spoken "
    "at once, and keep the whole reply to one or two short spoken sentences. Never use "
    "markdown, lists, headings, emojis or symbols that cannot be spoken aloud. Write numbers as "
    "words. "
    "If the caller only says something like hmm or okay, reply with a very short acknowledgement."
)
_HINDI_VOICE_RULES = (
    "You are speaking on a live voice call. Reply only in Hindi, written in Devanagari script. "
    + _VOICE_RULES
)
_ENGLISH_VOICE_RULES = (
    "You are speaking on a live voice call. Reply only in English. " + _VOICE_RULES
)
_GENERAL = "You are Dafter, a friendly general assistant. "
_SUPPORT = (
    "You are Dafter, a patient customer support agent who helps callers "
    "describe and solve their problem step by step. "
)

PERSONAS: dict[tuple[str, str], Persona] = {
    (DEFAULT_REF, "hi"): Persona(
        instructions=_GENERAL + _HINDI_VOICE_RULES,
        greeting="नमस्ते! मैं आपकी क्या मदद कर सकता हूँ?",
    ),
    ("persona://support/v3", "hi"): Persona(
        instructions=_SUPPORT + _HINDI_VOICE_RULES,
        greeting="नमस्ते! मैं सहायता टीम से बात कर रहा हूँ। बताइए, क्या समस्या है?",
    ),
    (DEFAULT_REF, "en"): Persona(
        instructions=_GENERAL + _ENGLISH_VOICE_RULES,
        greeting="Hello! How can I help you today?",
    ),
    ("persona://support/v3", "en"): Persona(
        instructions=_SUPPORT + _ENGLISH_VOICE_RULES,
        greeting="Hello, this is the support team. Tell me, what is the problem?",
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
