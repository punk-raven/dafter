from __future__ import annotations

import json
import re
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dafter_core.enums import EventType
from dafter_core.events import EventEnvelope
from dafter_core.hashing import seal
from dafter_providers import FileTranscript, Rendering

TRANSCRIPT_HASH = "transcriptHash"
LANGUAGE = re.compile(r"^[a-z]{2,3}(-[A-Za-z0-9]{2,8})*$")


@dataclass(frozen=True, slots=True)
class Source:
    recording_id: str
    speaker: dict[str, str]
    started_at: str
    stopped_at: str
    url: str

    @property
    def file_name(self) -> str:
        path = self.url.split("?", 1)[0]
        tail = path.rsplit("/", 1)[-1]
        suffix = tail[tail.rfind(".") :] if "." in tail else ".ogg"
        return self.recording_id + suffix

    @property
    def length_ms(self) -> int:
        return max(0, _ms_between(self.started_at, self.stopped_at))

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> Source:
        return cls(
            recording_id=d["recordingId"],
            speaker=dict(d["speaker"]),
            started_at=d["startedAt"],
            stopped_at=d["stoppedAt"],
            url=d["url"],
        )


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _ms_between(start: str, end: str) -> int:
    return round((_instant(end) - _instant(start)).total_seconds() * 1000)


def lines(sources: Sequence[Source], results: Mapping[str, FileTranscript]) -> list[dict[str, Any]]:
    out: list[tuple[float, str, int, dict[str, Any]]] = []
    for source in sources:
        result = results[source.file_name]
        spans = [(c.text, c.start_s * 1000, c.end_s * 1000) for c in result.chunks]
        if not spans and result.text:
            spans = [(result.text, 0.0, float(source.length_ms))]
        for i, (text, start, end) in enumerate(spans):
            line: dict[str, Any] = {
                "speaker": source.speaker,
                "recordingId": source.recording_id,
                "startMs": max(0, round(start)),
                "endMs": max(0, round(end)),
                "text": text,
            }
            if result.language and LANGUAGE.fullmatch(result.language):
                line["language"] = result.language
            at = _instant(source.started_at).timestamp() * 1000 + start
            out.append((at, source.recording_id, i, line))
    out.sort(key=lambda item: item[:3])
    return [line for *_, line in out]


def payload(
    *,
    language: str,
    provider: str,
    model: str,
    config_hash: str,
    created_at: datetime,
    sources: Sequence[Source],
    renderings: Mapping[Rendering, Mapping[str, FileTranscript]],
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "pass": "batch",
        "language": language,
        "provenance": {
            "provider": provider,
            "model": model,
            "configHash": config_hash,
            "createdAt": _rfc3339(created_at),
            "recordings": [
                {"recordingId": s.recording_id, "speaker": s.speaker, "startedAt": s.started_at}
                for s in sources
            ],
        },
        "verbatim": lines(sources, renderings["verbatim"]),
        "clean": lines(sources, renderings["clean"]),
    }
    sealed, _ = seal(json.dumps(doc), TRANSCRIPT_HASH)
    stamped: dict[str, Any] = json.loads(sealed)
    return stamped


def envelope(session_id: str, tenant_id: str, at: datetime, body: dict[str, Any]) -> EventEnvelope:
    event = EventEnvelope(
        event_id="e_" + secrets.token_hex(16),
        type=EventType.TRANSCRIPT_VERSION_CREATED,
        version=1,
        session_id=session_id,
        tenant_id=tenant_id,
        sequence=0,
        occurred_at=at,
        payload=body,
    )
    event.validate()
    return event


def _rfc3339(t: datetime) -> str:
    return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = ["TRANSCRIPT_HASH", "Source", "envelope", "lines", "payload"]
