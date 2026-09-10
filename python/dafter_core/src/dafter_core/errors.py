from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums import ErrorCode, Stage

_RETRYABLE = frozenset(
    {
        ErrorCode.PROVIDER_UNAVAILABLE,
        ErrorCode.PROVIDER_TIMEOUT,
        ErrorCode.RATE_LIMITED,
        ErrorCode.STREAM_CLOSED,
    }
)


@dataclass(frozen=True, slots=True)
class ProviderContext:
    name: str
    request_id: str | None = None
    native_code: str | None = None


@dataclass(frozen=True, slots=True)
class DafterError(Exception):
    """Message must stay safe to log: no name, email, phone or transcript content."""

    code: ErrorCode
    message: str
    stage: Stage | None = None
    provider: ProviderContext | None = None
    details: tuple[str, ...] = field(default_factory=tuple)

    @property
    def retryable(self) -> bool:
        return self.code in _RETRYABLE

    def to_dict(self) -> dict[str, Any]:
        """The wire form, matching schemas/errors/v1/error.schema.json."""
        d: dict[str, Any] = {
            "code": str(self.code),
            "message": self.message,
            "retryable": self.retryable,
        }
        if self.stage is not None:
            d["stage"] = str(self.stage)
        if self.provider is not None:
            p: dict[str, Any] = {"name": self.provider.name}
            if self.provider.request_id:
                p["requestId"] = self.provider.request_id
            if self.provider.native_code:
                p["nativeCode"] = self.provider.native_code
            d["provider"] = p
        if self.details:
            d["details"] = list(self.details)
        return d

    def __str__(self) -> str:
        head = f"{self.code}: {self.message}"
        if self.provider and self.provider.request_id:
            head += f" (provider {self.provider.name} request {self.provider.request_id})"
        return "\n  ".join([head, *self.details])
