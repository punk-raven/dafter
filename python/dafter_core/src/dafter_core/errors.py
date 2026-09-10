from __future__ import annotations

from dataclasses import dataclass, field

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

    def __str__(self) -> str:
        head = f"{self.code}: {self.message}"
        if self.provider and self.provider.request_id:
            head += f" (provider {self.provider.name} request {self.provider.request_id})"
        return "\n  ".join([head, *self.details])
