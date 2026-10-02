from __future__ import annotations

import os
from collections.abc import Mapping

from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError

SECRET_SCHEME = "secret://"


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


def resolve(ref: str | None, env: Mapping[str, str] | None = None) -> str:
    if not ref:
        raise DafterError(ErrorCode.AUTHENTICATION_FAILED, "the stage names no credential")
    name = env_name(ref)
    value = (os.environ if env is None else env).get(name, "")
    if not value:
        raise DafterError(
            ErrorCode.AUTHENTICATION_FAILED,
            f"credential {ref} is not in this worker's environment as {name}",
        )
    return value
