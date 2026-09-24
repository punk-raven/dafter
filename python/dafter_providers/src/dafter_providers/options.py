from __future__ import annotations

from collections.abc import Collection, Mapping
from typing import Any, TypeVar

from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError, ProviderContext

T = TypeVar("T")


class Options:
    def __init__(
        self, stage: Stage, provider: str, raw: Mapping[str, Any], known: Collection[str]
    ) -> None:
        self._stage = stage
        self._provider = provider
        self._raw = raw
        unknown = sorted(set(raw) - set(known))
        if unknown:
            raise self.error(
                f"{len(unknown)} option(s) this provider does not read",
                *(f"at '{self.pointer(k)}': not an option of {provider} {stage}" for k in unknown),
            )

    def pointer(self, key: str) -> str:
        return f"/agent/pipeline/{self._stage}/options/{key}"

    def error(self, message: str, *details: str) -> DafterError:
        return DafterError(
            ErrorCode.INVALID_CONFIG,
            message,
            stage=self._stage,
            provider=ProviderContext(self._provider),
            details=details,
        )

    def get(self, key: str, kind: type[T], default: T) -> T:
        value = self._raw.get(key, default)
        if kind is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, kind) or (kind is not bool and isinstance(value, bool)):
            raise self.error(
                "an option has the wrong type",
                f"at '{self.pointer(key)}': expected {kind.__name__}",
            )
        return value

    def choice(self, key: str, choices: Mapping[Any, T], default: Any) -> T:
        value = self._raw.get(key, default)
        if value not in choices:
            allowed = ", ".join(str(c) for c in choices)
            raise self.error(
                "an option names a value this provider does not offer",
                f"at '{self.pointer(key)}': one of {allowed}",
            )
        return choices[value]
