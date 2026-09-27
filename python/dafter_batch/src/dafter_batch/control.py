from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import aiohttp
from dafter_core import schemas
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError
from dafter_core.validation import validate_document

CONTROL_URL_ENV = "DAFTER_CONTROL_URL"
WORKER_SECRET_ENV = "DAFTER_WORKER_SECRET"
TIMEOUT = aiohttp.ClientTimeout(total=60)


def _unreachable(what: str, exc: BaseException) -> DafterError:
    return DafterError(
        ErrorCode.PROVIDER_UNAVAILABLE,
        f"the control plane could not be reached to {what}",
        stage=Stage.CONTROL,
        details=(f"at '/': {type(exc).__name__}",),
    )


def refused(raw: bytes, what: str) -> DafterError:
    try:
        doc = validate_document(schemas.ERROR, raw, ErrorCode.INTERNAL)
    except DafterError:
        return DafterError(
            ErrorCode.INTERNAL,
            f"the control plane refused to {what} without an error document",
            stage=Stage.CONTROL,
        )
    return DafterError(
        ErrorCode(doc["code"]),
        f"the control plane refused to {what}: {doc['message']}",
        stage=Stage.CONTROL,
        details=tuple(doc.get("details", ())),
    )


@dataclass(frozen=True, slots=True)
class ControlPlane:
    url: str
    secret: str

    @classmethod
    def from_env(cls) -> ControlPlane:
        url = os.environ.get(CONTROL_URL_ENV, "").rstrip("/")
        secret = os.environ.get(WORKER_SECRET_ENV, "")
        if not url or not secret:
            raise DafterError(
                ErrorCode.AUTHENTICATION_FAILED,
                "the batch pass needs the control plane's address and the worker credential",
                stage=Stage.CONTROL,
                details=(f"at '/': set {CONTROL_URL_ENV} and {WORKER_SECRET_ENV}",),
            )
        return cls(url, secret)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.secret}"}

    async def _json(self, method: str, path: str, what: str, body: Any = None) -> dict[str, Any]:
        try:
            async with (
                aiohttp.ClientSession(timeout=TIMEOUT) as http,
                http.request(method, self.url + path, json=body, headers=self._headers()) as r,
            ):
                raw = await r.read()
                status = r.status
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise _unreachable(what, exc) from exc
        if status >= 300:
            raise refused(raw, what)
        try:
            doc = json.loads(raw)
        except ValueError as exc:
            raise DafterError(
                ErrorCode.INTERNAL, f"the control plane answered {what} with no JSON"
            ) from exc
        if not isinstance(doc, dict):
            raise DafterError(ErrorCode.INTERNAL, f"the control plane answered {what} oddly")
        return doc

    async def sources(self, session_id: str) -> dict[str, Any]:
        return await self._json(
            "GET", f"/sessions/{session_id}/transcription/sources", "hand out the recordings"
        )

    async def submit(self, session_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        return await self._json(
            "POST", f"/sessions/{session_id}/transcripts", "store the transcript", envelope
        )


__all__ = ["ControlPlane", "refused"]
