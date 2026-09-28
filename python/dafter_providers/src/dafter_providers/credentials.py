from __future__ import annotations

import os
from collections.abc import Collection, Mapping

from dafter_core.config import ProviderRef
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError, ProviderContext

SECRET_SCHEME = "secret://"
PROVIDER_CREDENTIALS = frozenset(
    {
        "SARVAM_API_KEY",
        "GEMINI_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENCODE_API_KEY",
        "OPENAI_API_KEY",
        "NVIDIA_API_KEY",
    }
)


def env_name(ref: str) -> str:
    if not ref.startswith(SECRET_SCHEME):
        raise DafterError(ErrorCode.INVALID_CONFIG, "a credential is a secret:// reference")
    parts = [p for p in ref[len(SECRET_SCHEME) :].split("/") if p]
    if len(parts) < 2:
        raise DafterError(
            ErrorCode.INVALID_CONFIG,
            f"credential {ref} does not end in <vendor>/<name>, so it names no variable",
        )
    return "_".join(parts[-2:]).upper().replace("-", "_").replace(".", "_")


def resolve(
    ref: ProviderRef,
    stage: Stage,
    allowed: Collection[str],
    env: Mapping[str, str] | None = None,
    pointer: str | None = None,
) -> str:
    context = ProviderContext(ref.provider)
    if not ref.credential_ref:
        raise DafterError(
            ErrorCode.AUTHENTICATION_FAILED,
            "the stage names no credential",
            stage=stage,
            provider=context,
        )
    name = env_name(ref.credential_ref)
    readable = sorted(PROVIDER_CREDENTIALS.intersection(allowed))
    if name not in readable:
        raise DafterError(
            ErrorCode.INVALID_CONFIG,
            f"{ref.provider} {stage} reads only its own provider credential",
            stage=stage,
            provider=context,
            details=(
                f"at '{pointer or f'/agent/pipeline/{stage}'}/credentialRef': "
                f"a reference to {' or '.join(readable) or 'a provider key'}",
            ),
        )
    value = (os.environ if env is None else env).get(name, "")
    if not value:
        raise DafterError(
            ErrorCode.AUTHENTICATION_FAILED,
            f"credential {ref.credential_ref} is not in this worker's environment as {name}",
            stage=stage,
            provider=context,
        )
    return value
