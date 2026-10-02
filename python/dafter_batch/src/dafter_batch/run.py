from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

import aiohttp
from dafter_core.config import ProviderRef, ResolvedSessionConfig, parse
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError
from dafter_core.hashing import hash_document
from dafter_providers import RENDERINGS, AudioFile, BatchTranscriber, FileTranscript, batch_for

from .transcript import Source, envelope, payload

log = logging.getLogger("dafter.batch")

DOWNLOAD_TIMEOUT = aiohttp.ClientTimeout(total=600)
CONTENT_TYPES = {
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".mp4": "audio/mp4",
    ".wav": "audio/wav",
}

Fetch = Callable[[Source], Awaitable[AudioFile]]
Clock = Callable[[], datetime]


class Control(Protocol):
    async def sources(self, session_id: str) -> dict[str, Any]: ...

    async def submit(self, session_id: str, envelope: dict[str, Any]) -> dict[str, Any]: ...


def _refuse(code: ErrorCode, message: str, *details: str) -> DafterError:
    return DafterError(code, message, stage=Stage.STT, details=details)


def session_config(answer: Mapping[str, Any]) -> ResolvedSessionConfig:
    raw = answer.get("config")
    document = raw if isinstance(raw, str) else json.dumps(raw)
    cfg = parse(document)
    if cfg.config_hash != answer.get("configHash") or cfg.config_hash != hash_document(document):
        raise _refuse(
            ErrorCode.INVALID_CONFIG,
            "the stored config does not match its own hash",
            "at '/configHash': recomputed over RFC 8785 and does not match",
        )
    if not cfg.transcription.after_call:
        raise _refuse(
            ErrorCode.INVALID_CONFIG,
            "the session's stored config does not transcribe after the call",
            "at '/transcription/mode': must be after_call or both",
        )
    return cfg


def ready(answer: Mapping[str, Any]) -> list[Source]:
    pending = answer.get("pending") or []
    if pending:
        raise _refuse(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"{len(pending)} recording(s) of this session are not finished yet; run the pass again",
            *(f"at '/recordings/{p['recordingId']}': {p['reason']}" for p in pending),
        )
    for skipped in answer.get("skipped") or []:
        log.info(
            "recording left out",
            extra={"recording": skipped["recordingId"], "why": skipped["reason"]},
        )
    sources = [Source.from_dict(s) for s in answer.get("sources") or []]
    if not sources:
        raise _refuse(
            ErrorCode.INVALID_CONFIG,
            "the session has no finished, attributed audio track recording to transcribe",
            "at '/recording': start a track recording for each participant during the call",
        )
    return sources


async def download(source: Source) -> AudioFile:
    try:
        async with (
            aiohttp.ClientSession(timeout=DOWNLOAD_TIMEOUT) as http,
            http.get(source.url) as r,
        ):
            if r.status != 200:
                raise _refuse(
                    ErrorCode.PROVIDER_UNAVAILABLE,
                    "a recording could not be read back from storage",
                    f"at '/recordings/{source.recording_id}': http {r.status}",
                )
            data = await r.read()
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise _refuse(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "a recording could not be read back from storage",
            f"at '/recordings/{source.recording_id}': {type(exc).__name__}",
        ) from exc
    suffix = source.file_name[len(source.recording_id) :]
    return AudioFile(source.file_name, data, CONTENT_TYPES.get(suffix, "application/octet-stream"))


async def transcribe_session(
    session_id: str,
    control: Control,
    transcriber: Callable[[ProviderRef | None], BatchTranscriber] = batch_for,
    fetch: Fetch = download,
    clock: Clock = lambda: datetime.now(UTC),
) -> dict[str, Any]:
    answer = await control.sources(session_id)
    cfg = session_config(answer)
    sources = ready(answer)
    batch = transcriber(cfg.transcription.batch)
    files: Sequence[AudioFile] = await asyncio.gather(*(fetch(s) for s in sources))
    results: list[Mapping[str, FileTranscript]] = await asyncio.gather(
        *(batch.transcribe(files, cfg.language, rendering) for rendering in RENDERINGS)
    )
    now = clock()
    body = payload(
        language=cfg.language,
        provider=batch.provider,
        model=batch.model,
        config_hash=cfg.config_hash or "",
        created_at=now,
        sources=sources,
        renderings=dict(zip(RENDERINGS, results, strict=True)),
    )
    event = envelope(cfg.session_id, cfg.tenant_id, now, body)
    stored = await control.submit(session_id, event.to_dict())
    log.info(
        "transcript version stored",
        extra={
            "session": session_id,
            "version": stored.get("version"),
            "hash": body["transcriptHash"],
            "recordings": len(sources),
        },
    )
    return stored


__all__ = ["Control", "download", "ready", "session_config", "transcribe_session"]
