from __future__ import annotations

from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError, ProviderContext

NAME = "sarvam"

LANGUAGES = {
    "hi": "hi-IN",
    "hi-IN": "hi-IN",
    "en-IN": "en-IN",
    "bn-IN": "bn-IN",
    "kn-IN": "kn-IN",
    "ml-IN": "ml-IN",
    "mr-IN": "mr-IN",
    "ta-IN": "ta-IN",
    "te-IN": "te-IN",
    "gu-IN": "gu-IN",
    "pa-IN": "pa-IN",
    "or-IN": "or-IN",
}


def language_code(tag: str, stage: Stage) -> str:
    code = LANGUAGES.get(tag)
    if code is None:
        raise DafterError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"{NAME} {stage} does not serve this session's language",
            stage=stage,
            provider=ProviderContext(NAME),
            details=("at '/language': not a language this provider declares",),
        )
    return code


__all__ = ["LANGUAGES", "language_code"]
