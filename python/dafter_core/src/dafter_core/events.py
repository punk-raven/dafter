from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from . import schemas
from .enums import ErrorCode, EventType
from .errors import DafterError
from .validation import validate_document


@dataclass(frozen=True, slots=True)
class EventEnvelope:
    event_id: str
    type: EventType
    version: int
    session_id: str
    tenant_id: str
    sequence: int
    occurred_at: datetime
    trace_id: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "eventId": self.event_id,
            "type": str(self.type),
            "version": self.version,
            "sessionId": self.session_id,
            "tenantId": self.tenant_id,
            "sequence": self.sequence,
            "occurredAt": _rfc3339(self.occurred_at),
        }
        if self.trace_id:
            d["traceId"] = self.trace_id
        if self.payload:
            d["payload"] = self.payload
        return d

    def validate(self) -> None:
        """A malformed event is a platform fault, not a consumer's config error."""
        validate_document(
            schemas.EVENT_ENVELOPE, json.dumps(self.to_dict()).encode(), ErrorCode.INTERNAL
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> EventEnvelope:
        return cls(
            event_id=d["eventId"],
            type=EventType(d["type"]),
            version=d["version"],
            session_id=d["sessionId"],
            tenant_id=d["tenantId"],
            sequence=d["sequence"],
            occurred_at=datetime.fromisoformat(d["occurredAt"]),
            trace_id=d.get("traceId"),
            payload=d.get("payload") or {},
        )


def parse_event(raw: bytes | str) -> EventEnvelope:
    if isinstance(raw, str):
        raw = raw.encode()
    doc = validate_document(schemas.EVENT_ENVELOPE, raw, ErrorCode.INTERNAL)
    return EventEnvelope.from_dict(doc)


def _rfc3339(t: datetime) -> str:
    if t.tzinfo is None:
        raise DafterError(
            ErrorCode.INTERNAL,
            "occurredAt has no timezone; an event timestamp without an offset is ambiguous",
        )
    return t.isoformat().replace("+00:00", "Z")
