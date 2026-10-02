from __future__ import annotations

from dataclasses import dataclass

from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError

DEFAULT_REF = "persona://default"


@dataclass(frozen=True, slots=True)
class Persona:
    instructions: str
    greeting: str


@dataclass(frozen=True, slots=True)
class Script:
    role: str
    rules: str
    greeting: str
    introduction: str

    def spoken_by(self, name: str | None) -> Persona:
        if not name:
            return Persona(
                instructions=f"You are {self.role}. {FEMININE} {self.rules}",
                greeting=self.greeting,
            )
        return Persona(
            instructions=f"You are {name}, {self.role}. {FEMININE} {self.rules}",
            greeting=self.introduction.format(name=name),
        )


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
_GENERAL = "a friendly general assistant"
_SUPPORT = (
    "a patient customer support agent who helps callers describe and solve their problem "
    "step by step"
)

FEMININE = "You are a woman: whenever you speak about yourself, use feminine grammar."
_HINDI_FEMININE = " In Hindi say मैं कर सकती हूँ, मैं देख रही हूँ, मैं बताती हूँ, never सकता, रहा or बताता."

SCRIPTS: dict[tuple[str, str], Script] = {
    (DEFAULT_REF, "hi"): Script(
        role=_GENERAL,
        rules=_HINDI_VOICE_RULES + _HINDI_FEMININE,
        greeting="नमस्ते! मैं आपकी क्या मदद कर सकती हूँ?",
        introduction="नमस्ते! मैं {name} हूँ। मैं आपकी क्या मदद कर सकती हूँ?",
    ),
    ("persona://support/v3", "hi"): Script(
        role=_SUPPORT,
        rules=_HINDI_VOICE_RULES + _HINDI_FEMININE,
        greeting="नमस्ते! मैं सहायता टीम से बात कर रही हूँ। बताइए, क्या समस्या है?",
        introduction="नमस्ते! मैं {name}, सहायता टीम से बात कर रही हूँ। बताइए, क्या समस्या है?",
    ),
    (DEFAULT_REF, "en"): Script(
        role=_GENERAL,
        rules=_ENGLISH_VOICE_RULES,
        greeting="Hello! How can I help you today?",
        introduction="Hello! I'm {name}. How can I help you today?",
    ),
    ("persona://support/v3", "en"): Script(
        role=_SUPPORT,
        rules=_ENGLISH_VOICE_RULES,
        greeting="Hello, this is the support team. Tell me, what is the problem?",
        introduction="Hello, this is {name} from the support team. Tell me, what is the problem?",
    ),
}


def called_by_name(persona: Persona) -> Persona:
    return Persona(
        instructions=(
            f"{persona.instructions}\n\nYou are in a call with several people and you speak "
            "only when one of them calls you by your name. Each message starts with who "
            "spoke in square brackets: lines marked 'to you' are the person talking to "
            "you, who is the one you answer; lines marked 'not to you' are what others said "
            "in the call just before, which you may use as context but never answer or quote. "
            "Call go_quiet when the person talking to you is done or asks you to be quiet."
        ),
        greeting=persona.greeting,
    )


def base_language(tag: str) -> str:
    return tag.split("-", 1)[0].lower()


def persona_for(ref: str | None, language: str, name: str | None) -> Persona:
    key = (ref or DEFAULT_REF, base_language(language))
    script = SCRIPTS.get(key)
    if script is None:
        raise DafterError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "no persona document is available for this reference and language",
            details=("at '/agent/personaRef': not registered in this worker for the language",),
        )
    return script.spoken_by(name)
