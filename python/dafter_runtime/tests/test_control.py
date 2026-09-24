from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from dafter_core.config import ResolvedSessionConfig
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError
from dafter_core.hashing import seal
from dafter_runtime.control import ControlPlane, encryption, session_key
from dafter_runtime.plan import load
from livekit import rtc

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
SECRET = "worker-secret-for-tests"
KEY = bytes(range(32))

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def trusted() -> ResolvedSessionConfig:
    doc = json.loads(JOB.read_bytes())
    doc["privacyMode"] = "trusted_agent"
    doc["media"]["encryption"] = {"mode": "e2ee", "keyModel": "server_shared"}
    sealed, _ = seal(json.dumps(doc))
    return load(sealed)


def encoded(key: bytes) -> str:
    return base64.urlsafe_b64encode(key).rstrip(b"=").decode()


async def serving(handler: Handler, call: Callable[[ControlPlane], Awaitable[Any]]) -> Any:
    app = web.Application()
    app.router.add_post("/sessions/{session}/agent/{action}", handler)
    server = TestServer(app, host="127.0.0.1")
    await server.start_server()
    try:
        return await call(ControlPlane(str(server.make_url("")).rstrip("/"), SECRET))
    finally:
        await server.close()


def test_the_worker_fetches_its_session_key_with_its_own_credential() -> None:
    cfg = trusted()
    seen: dict[str, Any] = {}

    async def handler(req: web.Request) -> web.Response:
        seen.update(path=req.path, auth=req.headers.get("Authorization"), body=await req.json())
        return web.json_response({"sessionId": cfg.session_id, "encryptionKey": encoded(KEY)})

    key = asyncio.run(serving(handler, lambda c: c.session_key(cfg)))
    assert key == KEY
    assert seen == {
        "path": f"/sessions/{cfg.session_id}/agent/key",
        "auth": f"Bearer {SECRET}",
        "body": {"configHash": cfg.config_hash},
    }


def test_a_withheld_key_carries_the_control_plane_reason() -> None:
    async def handler(req: web.Request) -> web.Response:
        return web.json_response(
            {
                "code": "privacy_mode_forbids",
                "message": "1 problem with the request",
                "retryable": False,
                "details": ["at '/privacyMode': never discloses the session key to an agent"],
            },
            status=400,
        )

    with pytest.raises(DafterError) as caught:
        asyncio.run(serving(handler, lambda c: c.session_key(trusted())))
    assert caught.value.code is ErrorCode.PRIVACY_MODE_FORBIDS
    assert caught.value.stage is Stage.CONTROL
    assert "/privacyMode" in caught.value.details[0]


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        json.dumps({"sessionId": "s_00000000", "encryptionKey": encoded(KEY)}).encode(),
        json.dumps({"sessionId": "SESSION", "encryptionKey": encoded(KEY[:16])}).encode(),
        json.dumps({"sessionId": "SESSION", "encryptionKey": "***"}).encode(),
        json.dumps({"sessionId": "SESSION", "encryptionKey": encoded(KEY), "x": 1}).encode(),
    ],
)
def test_an_answer_that_is_not_this_sessions_key_is_refused(body: bytes) -> None:
    cfg = trusted()
    with pytest.raises(DafterError) as caught:
        session_key(body.replace(b"SESSION", cfg.session_id.encode()), cfg.session_id)
    assert caught.value.code is ErrorCode.INTERNAL
    assert "/encryptionKey" in caught.value.details[0]


def test_an_unreachable_control_plane_is_a_located_failure() -> None:
    with pytest.raises(DafterError) as caught:
        asyncio.run(ControlPlane("http://127.0.0.1:9", SECRET).session_key(trusted()))
    assert caught.value.code is ErrorCode.INTERNAL
    assert caught.value.stage is Stage.CONTROL


def test_a_refusal_reaches_the_control_plane_as_an_error_document() -> None:
    seen: dict[str, Any] = {}
    refusal = DafterError(
        ErrorCode.UNSUPPORTED_CAPABILITY,
        "this worker runs the cascaded pipeline only",
        details=("at '/agent/mode': speech_to_speech",),
    )

    async def handler(req: web.Request) -> web.Response:
        seen.update(path=req.path, auth=req.headers.get("Authorization"), body=await req.json())
        return web.Response(status=204)

    asyncio.run(serving(handler, lambda c: c.report_refusal("s_7f3a9c21", refusal)))
    assert seen == {
        "path": "/sessions/s_7f3a9c21/agent/refusal",
        "auth": f"Bearer {SECRET}",
        "body": refusal.to_dict(),
    }


def test_a_refusal_that_cannot_be_delivered_does_not_raise() -> None:
    refusal = DafterError(ErrorCode.INTERNAL, "x")
    asyncio.run(ControlPlane("http://127.0.0.1:9", SECRET).report_refusal("s_7f3a9c21", refusal))


def test_the_control_plane_is_configured_only_with_both_its_url_and_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DAFTER_WORKER_SECRET", raising=False)
    monkeypatch.setenv("DAFTER_CONTROL_URL", "http://127.0.0.1:8080/")
    assert ControlPlane.from_env() is None
    monkeypatch.setenv("DAFTER_WORKER_SECRET", SECRET)
    assert ControlPlane.from_env() == ControlPlane("http://127.0.0.1:8080", SECRET)


def test_the_frame_cryptor_matches_the_browser_key_provider() -> None:
    opts = encryption(KEY).key_provider_options
    assert opts.shared_key == KEY
    assert opts.key_derivation_function == rtc.KeyDerivationFunction.HKDF
    assert opts.ratchet_salt == b"LKFrameEncryptionKey"
    assert (opts.ratchet_window_size, opts.failure_tolerance) == (0, -1)
