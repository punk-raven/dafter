from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from dafter_batch.__main__ import arguments
from dafter_batch.control import ControlPlane
from dafter_batch.run import download
from dafter_batch.transcript import Source
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError

SECRET = "worker-secret-for-tests"
REFUSED_AT = "at '/transcription/mode': the transcript after the call is off"
REFUSAL = {
    "code": "invalid_config",
    "message": "1 problem with the request",
    "retryable": False,
    "details": [REFUSED_AT],
}


def served(
    routes: Callable[[web.Application], None],
    body: Callable[[str], Awaitable[Any]],
) -> Any:
    async def go() -> Any:
        app = web.Application()
        routes(app)
        server = TestServer(app)
        await server.start_server()
        try:
            return await body(str(server.make_url("")).rstrip("/"))
        finally:
            await server.close()

    return asyncio.run(go())


def test_the_pass_speaks_to_the_control_plane_with_the_worker_credential() -> None:
    seen: list[tuple[str, str, Any]] = []

    async def sources(request: web.Request) -> web.Response:
        seen.append((request.method, request.headers.get("Authorization", ""), None))
        return web.json_response({"sessionId": request.match_info["id"], "sources": []})

    async def submit(request: web.Request) -> web.Response:
        seen.append(
            (request.method, request.headers.get("Authorization", ""), await request.json())
        )
        return web.json_response({"version": 3}, status=201)

    def routes(app: web.Application) -> None:
        app.router.add_get("/sessions/{id}/transcription/sources", sources)
        app.router.add_post("/sessions/{id}/transcripts", submit)

    async def body(url: str) -> tuple[dict[str, Any], dict[str, Any]]:
        control = ControlPlane(url, SECRET)
        return await control.sources("s_7f3a9c21"), await control.submit(
            "s_7f3a9c21", {"type": "transcript.version_created"}
        )

    got, stored = served(routes, body)
    assert got == {"sessionId": "s_7f3a9c21", "sources": []}
    assert stored == {"version": 3}
    assert seen == [
        ("GET", f"Bearer {SECRET}", None),
        ("POST", f"Bearer {SECRET}", {"type": "transcript.version_created"}),
    ]


def test_a_refusal_comes_back_as_the_control_planes_own_error() -> None:
    async def refuse(request: web.Request) -> web.Response:
        return web.json_response(REFUSAL, status=400)

    def routes(app: web.Application) -> None:
        app.router.add_get("/sessions/{id}/transcription/sources", refuse)

    async def body(url: str) -> None:
        await ControlPlane(url, SECRET).sources("s_7f3a9c21")

    with pytest.raises(DafterError) as caught:
        served(routes, body)
    assert caught.value.code is ErrorCode.INVALID_CONFIG
    assert caught.value.details == (REFUSED_AT,)


def test_the_control_plane_is_required_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DAFTER_WORKER_SECRET", raising=False)
    monkeypatch.setenv("DAFTER_CONTROL_URL", "http://127.0.0.1:8080/")
    with pytest.raises(DafterError) as caught:
        ControlPlane.from_env()
    assert caught.value.code is ErrorCode.AUTHENTICATION_FAILED
    monkeypatch.setenv("DAFTER_WORKER_SECRET", SECRET)
    assert ControlPlane.from_env() == ControlPlane("http://127.0.0.1:8080", SECRET)


def source(url: str, path: str) -> Source:
    return Source(
        recording_id="EG_Kx9r2QmT4bZa",
        speaker={"kind": "human", "participantId": "p_4b81e0d7"},
        started_at="2026-09-24T10:00:01.5Z",
        stopped_at="2026-09-24T10:30:00Z",
        url=f"{url}{path}?X-Amz-Signature=a",
    )


def test_a_recording_is_read_back_from_its_presigned_url() -> None:
    queries: list[str] = []

    async def obj(request: web.Request) -> web.Response:
        queries.append(request.query_string)
        if request.match_info["key"].endswith("missing.ogg"):
            return web.Response(status=403)
        return web.Response(body=b"OggS audio", content_type="audio/ogg")

    def routes(app: web.Application) -> None:
        app.router.add_get("/dafter-recordings/{key:.*}", obj)

    async def body(url: str) -> tuple[Any, ...]:
        found = await download(source(url, "/dafter-recordings/s_7f3a9c21/track-TR_a-1.ogg"))
        try:
            await download(source(url, "/dafter-recordings/s_7f3a9c21/missing.ogg"))
        except DafterError as exc:
            return found, exc
        return found, None

    found, refused = served(routes, body)
    assert (found.name, found.data, found.content_type) == (
        "EG_Kx9r2QmT4bZa.ogg",
        b"OggS audio",
        "audio/ogg",
    )
    assert queries[0] == "X-Amz-Signature=a"
    assert isinstance(refused, DafterError) and refused.code is ErrorCode.PROVIDER_UNAVAILABLE
    assert json.dumps(refused.to_dict()).count("OggS") == 0


def test_the_command_takes_one_session_id() -> None:
    assert arguments(["s_7f3a9c21"]).session == "s_7f3a9c21"
    with pytest.raises(SystemExit):
        arguments(["Asha's call"])
