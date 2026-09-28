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
    "at once, and keep the whole reply to one or two short spoken sentences. Say things "
    "the way you would say them aloud: no lists, no numbering, no headings, no markdown, "
    "no emojis and no symbols that cannot be spoken; if there are several steps, say the "
    "first one and offer the next. "
)
_ACKNOWLEDGE = (
    "If the caller only says something like hmm or okay, reply with a very short acknowledgement."
)
_HINDI_VOICE_RULES = (
    "You are speaking on a live voice call. Reply only in Hindi, written in Devanagari script, "
    "the way a polite person talks: always address the caller as aap, never tum or tu. "
    + _VOICE_RULES
    + "Write numbers, amounts, dates, times and phone numbers in digits, for example "
    "₹1,25,000, 25/12/2025, 5:30 or 98765 43210; they are read out for you. " + _ACKNOWLEDGE
)
_ENGLISH_VOICE_RULES = (
    "You are speaking on a live voice call. Reply only in English. "
    + _VOICE_RULES
    + "Write numbers as words. "
    + _ACKNOWLEDGE
)


def _spoken_in(language: str, script: str, polite: str, familiar: str) -> str:
    return (
        f"You are speaking on a live voice call. Reply only in {language}, written in {script} "
        f"script, the way a polite person talks: always address the caller as {polite}, never "
        f"{familiar}. "
        + _VOICE_RULES
        + f"Write numbers, amounts, dates and times as {language} words, the way they are said "
        "aloud, and phone numbers digit by digit in words; never write digits. " + _ACKNOWLEDGE
    )


_KANNADA_VOICE_RULES = _spoken_in("Kannada", "Kannada", "ನೀವು", "ನೀನು")
_MARATHI_VOICE_RULES = _spoken_in("Marathi", "Devanagari", "तुम्ही or आपण", "तू")
_TELUGU_VOICE_RULES = _spoken_in("Telugu", "Telugu", "మీరు", "నువ్వు")
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
    (DEFAULT_REF, "kn"): Persona(
        instructions=_GENERAL + _KANNADA_VOICE_RULES,
        greeting="ನಮಸ್ಕಾರ! ನಾನು ನಿಮಗೆ ಹೇಗೆ ಸಹಾಯ ಮಾಡಬಹುದು?",
    ),
    ("persona://support/v3", "kn"): Persona(
        instructions=_SUPPORT + _KANNADA_VOICE_RULES,
        greeting="ನಮಸ್ಕಾರ! ನಾನು ಸಹಾಯ ತಂಡದಿಂದ ಮಾತನಾಡುತ್ತಿದ್ದೇನೆ. ಹೇಳಿ, ಏನು ಸಮಸ್ಯೆ?",
    ),
    (DEFAULT_REF, "mr"): Persona(
        instructions=_GENERAL + _MARATHI_VOICE_RULES,
        greeting="नमस्कार! मी तुमची काय मदत करू शकतो?",
    ),
    ("persona://support/v3", "mr"): Persona(
        instructions=_SUPPORT + _MARATHI_VOICE_RULES,
        greeting="नमस्कार! मी सहाय्य टीममधून बोलतोय. सांगा, काय अडचण आहे?",
    ),
    (DEFAULT_REF, "te"): Persona(
        instructions=_GENERAL + _TELUGU_VOICE_RULES,
        greeting="నమస్కారం! నేను మీకు ఎలా సహాయం చేయగలను?",
    ),
    ("persona://support/v3", "te"): Persona(
        instructions=_SUPPORT + _TELUGU_VOICE_RULES,
        greeting="నమస్కారం! నేను సహాయ బృందం నుండి మాట్లాడుతున్నాను. చెప్పండి, సమస్య ఏమిటి?",
    ),
}


def called_by_name(persona: Persona, name: str) -> Persona:
    return Persona(
        instructions=(
            f"{persona.instructions}\n\nYour name is {name}. You are in a call with several "
            "people and you speak only when one of them calls you. Each message starts with "
            "who spoke in square brackets: lines marked 'to you' are the person talking to "
            "you, who is the one you answer; lines marked 'not to you' are what others said "
            "in the call just before, which you may use as context but never answer or quote. "
            "Call go_quiet when the person talking to you is done or asks you to be quiet."
        ),
        greeting=persona.greeting,
    )


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
