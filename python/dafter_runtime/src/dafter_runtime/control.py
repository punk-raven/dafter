from __future__ import annotations

import base64
import binascii
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

import aiohttp
from dafter_core import schemas
from dafter_core.config import ResolvedSessionConfig
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError
from dafter_core.validation import validate_document
from livekit import rtc

CONTROL_URL_ENV = "DAFTER_CONTROL_URL"
WORKER_SECRET_ENV = "DAFTER_WORKER_SECRET"
KEY_BYTES = 32
TIMEOUT = aiohttp.ClientTimeout(total=5)

log = logging.getLogger("dafter.runtime.control")


def _unreachable(what: str, exc: BaseException) -> DafterError:
    return DafterError(
        ErrorCode.INTERNAL,
        f"the control plane could not be reached to {what}",
        stage=Stage.CONTROL,
        details=(f"at '/': {type(exc).__name__}",),
    )


def _malformed(because: str) -> DafterError:
    return DafterError(
        ErrorCode.INTERNAL,
        "the control plane answered the key request with something that is not a session key",
        stage=Stage.CONTROL,
        details=(f"at '/encryptionKey': {because}",),
    )


def withheld(raw: bytes) -> DafterError:
    doc = validate_document(schemas.ERROR, raw, ErrorCode.INTERNAL)
    return DafterError(
        ErrorCode(doc["code"]),
        f"the control plane withheld the session key: {doc['message']}",
        stage=Stage.CONTROL,
        details=tuple(doc.get("details", ())),
    )


def session_key(raw: bytes, session_id: str) -> bytes:
    try:
        doc: Any = json.loads(raw)
    except ValueError as exc:
        raise _malformed("the answer is not JSON") from exc
    if not isinstance(doc, dict) or set(doc) != {"sessionId", "encryptionKey"}:
        raise _malformed("the answer carries fields other than sessionId and encryptionKey")
    if doc["sessionId"] != session_id:
        raise _malformed("the key names another session")
    encoded = doc["encryptionKey"]
    if not isinstance(encoded, str):
        raise _malformed("not a string")
    try:
        key = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
    except (binascii.Error, ValueError) as exc:
        raise _malformed("not base64url") from exc
    if len(key) != KEY_BYTES:
        raise _malformed(f"{len(key)} bytes, want {KEY_BYTES}")
    return key


def encryption(key: bytes) -> rtc.E2EEOptions:
    return rtc.E2EEOptions(
        key_provider_options=rtc.KeyProviderOptions(
            shared_key=key,
            ratchet_window_size=0,
            failure_tolerance=-1,
            key_derivation_function=rtc.KeyDerivationFunction.HKDF,
        ),
        encryption_type=rtc.EncryptionType.GCM,
    )


@dataclass(frozen=True, slots=True)
class ControlPlane:
    url: str
    secret: str

    @classmethod
    def from_env(cls) -> ControlPlane | None:
        url = os.environ.get(CONTROL_URL_ENV, "").rstrip("/")
        secret = os.environ.get(WORKER_SECRET_ENV, "")
        return cls(url, secret) if url and secret else None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.secret}"}

    async def session_key(self, cfg: ResolvedSessionConfig) -> bytes:
        url = f"{self.url}/sessions/{cfg.session_id}/agent/key"
        try:
            async with (
                aiohttp.ClientSession(timeout=TIMEOUT) as http,
                http.post(url, json={"configHash": cfg.config_hash}, headers=self._headers()) as r,
            ):
                body = await r.read()
                status = r.status
        except (aiohttp.ClientError, TimeoutError) as exc:
            raise _unreachable("fetch the session key", exc) from exc
        if status != 200:
            raise withheld(body)
        return session_key(body, cfg.session_id)

    async def report_refusal(self, session_id: str, refusal: DafterError) -> None:
        url = f"{self.url}/sessions/{session_id}/agent/refusal"
        try:
            async with (
                aiohttp.ClientSession(timeout=TIMEOUT) as http,
                http.post(url, json=refusal.to_dict(), headers=self._headers()) as r,
            ):
                status = r.status
        except (aiohttp.ClientError, TimeoutError) as exc:
            log.warning("refusal not reported", extra={"error": type(exc).__name__})
            return
        if status != 204:
            log.warning("refusal not recorded", extra={"status": status})
