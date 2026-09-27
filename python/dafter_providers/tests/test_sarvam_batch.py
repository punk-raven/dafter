from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from dafter_core.config import ProviderRef
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError
from dafter_providers import AudioFile, FileTranscript, Rendering, batch_for
from dafter_providers.sarvam.batch import SarvamBatch, build_batch, chunks_of

VECTORS = Path(__file__).resolve().parents[3] / "testdata" / "sarvam-batch"
KEY = "test-only-not-a-key"
KEY_REF = "secret://tenants/t_9c21a4be/sarvam/api-key"
JOB = "20260924_9f8b7c6d-5e4a-3b2c-1d0e-000000000001"
ASHA = AudioFile("EG_Kx9r2QmT4bZa.ogg", b"OggS asha", "audio/ogg")
RAVI = AudioFile("EG_Lw3s8PnV6cYb.ogg", b"OggS ravi", "audio/ogg")


@pytest.fixture(autouse=True)
def sarvam_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARVAM_API_KEY", KEY)


def vector(name: str, storage: str = "") -> Any:
    return json.loads((VECTORS / name).read_text().replace("{storage}", storage))


@dataclass
class Stub:
    status_replies: list[str] = field(
        default_factory=lambda: ["status-running.response.json", "status-completed.response.json"]
    )
    refuse: dict[str, int] = field(default_factory=dict)
    uploads: dict[str, tuple[bytes, str, str]] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    bodies: dict[str, Any] = field(default_factory=dict)
    storage: str = ""

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/speech-to-text/job/v1", self._api("create", "create-job"))
        app.router.add_post(
            "/speech-to-text/job/v1/upload-files", self._api("upload", "upload-files")
        )
        app.router.add_post(
            "/speech-to-text/job/v1/download-files", self._api("download", "download-files")
        )
        app.router.add_post("/speech-to-text/job/v1/{job}/start", self._api("start", "start"))
        app.router.add_get("/speech-to-text/job/v1/{job}/status", self._status)
        app.router.add_put("/upload/{name}", self._put)
        app.router.add_get("/download/{name}", self._get)
        return app

    def _api(self, name: str, vec: str) -> Callable[[web.Request], Awaitable[web.Response]]:
        async def handle(request: web.Request) -> web.Response:
            self.calls.append(name)
            if request.headers.get("api-subscription-key") != KEY:
                return web.json_response({"message": "no key"}, status=403)
            if name in self.refuse:
                return web.json_response({"message": "refused"}, status=self.refuse[name])
            if name != "start":
                self.bodies[name] = await request.json()
            elif await request.read():
                return web.json_response({"message": "start takes no body"}, status=400)
            if name == "start" and request.match_info["job"] != JOB:
                return web.json_response({"message": "unknown job"}, status=404)
            return web.json_response(vector(f"{vec}.response.json", self.storage))

        return handle

    async def _status(self, request: web.Request) -> web.Response:
        self.calls.append("status")
        reply = (
            self.status_replies.pop(0) if len(self.status_replies) > 1 else self.status_replies[0]
        )
        return web.json_response(vector(reply))

    async def _put(self, request: web.Request) -> web.Response:
        self.calls.append("put")
        self.uploads[request.match_info["name"]] = (
            await request.read(),
            request.headers.get("x-ms-blob-type", ""),
            request.headers.get("Content-Type", ""),
        )
        return web.Response(status=201)

    async def _get(self, request: web.Request) -> web.Response:
        self.calls.append("get")
        index = request.match_info["name"].split(".")[0]
        return web.json_response(vector(f"output-{index}.json"))


async def no_wait(seconds: float) -> None:
    return None


def run(
    stub: Stub,
    files: list[AudioFile],
    rendering: Rendering = "verbatim",
    clock: Callable[[], float] | None = None,
) -> dict[str, FileTranscript]:
    async def go() -> dict[str, FileTranscript]:
        server = TestServer(stub.app())
        await server.start_server()
        stub.storage = str(server.make_url("")).rstrip("/")
        try:
            batch = SarvamBatch(
                KEY,
                "saaras:v3",
                600,
                base_url=stub.storage,
                sleep=no_wait,
                clock=clock or (lambda: 0.0),
            )
            return dict(await batch.transcribe(files, "hi", rendering))
        finally:
            await server.close()

    return asyncio.run(go())


def test_one_job_uploads_every_track_and_reads_back_its_timed_chunks() -> None:
    stub = Stub()
    got = run(stub, [ASHA, RAVI])
    assert stub.calls == [
        "create",
        "upload",
        "put",
        "put",
        "start",
        "status",
        "status",
        "download",
        "get",
        "get",
    ]
    assert stub.bodies["create"] == vector("create-job.request.json")
    assert stub.bodies["upload"] == vector("upload-files.request.json")
    assert stub.bodies["download"] == vector("download-files.request.json")
    assert stub.uploads == {
        ASHA.name: (ASHA.data, "BlockBlob", "audio/ogg"),
        RAVI.name: (RAVI.data, "BlockBlob", "audio/ogg"),
    }
    asha = got[ASHA.name]
    assert asha.language == "hi-IN"
    assert asha.text == "मुझे कल की मीटिंग का टाइम बताओ उम्म और एजेंडा भी"
    assert [(c.text, c.start_s, c.end_s) for c in asha.chunks] == [
        ("मुझे कल की मीटिंग का टाइम बताओ", 1.21, 3.48),
        ("उम्म और एजेंडा भी", 4.02, 5.9),
    ]
    assert got[RAVI.name].chunks[0].text == "दस बजे है ना"


def test_the_clean_rendering_is_sarvams_transcribe_mode() -> None:
    stub = Stub()
    run(stub, [ASHA, RAVI], "clean")
    assert stub.bodies["create"]["job_parameters"]["mode"] == "transcribe"


def test_more_than_twenty_tracks_run_as_several_jobs(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs: list[list[str]] = []

    async def job(
        self: SarvamBatch, http: Any, files: list[AudioFile], language: str, rendering: Rendering
    ) -> dict[str, FileTranscript]:
        jobs.append([f.name for f in files])
        return {}

    monkeypatch.setattr(SarvamBatch, "_job", job)
    files = [AudioFile(f"EG_t{i:02d}.ogg", b"OggS", "audio/ogg") for i in range(45)]
    run(Stub(), files)
    assert [len(j) for j in jobs] == [20, 20, 5]
    assert [name for job in jobs for name in job] == [f.name for f in files]


@pytest.mark.parametrize(
    ("step", "status", "code"),
    [
        ("create", 403, ErrorCode.AUTHENTICATION_FAILED),
        ("create", 429, ErrorCode.QUOTA_EXCEEDED),
        ("upload", 422, ErrorCode.INVALID_CONFIG),
        ("start", 503, ErrorCode.PROVIDER_UNAVAILABLE),
    ],
)
def test_a_refusal_maps_to_the_taxonomy(step: str, status: int, code: ErrorCode) -> None:
    with pytest.raises(DafterError) as caught:
        run(Stub(refuse={step: status}), [ASHA, RAVI])
    assert caught.value.code is code
    assert caught.value.provider is not None
    assert caught.value.provider.native_code == str(status)


def test_a_failed_job_or_a_failed_file_is_reported_by_recording() -> None:
    failed = vector("status-completed.response.json")
    failed["job_details"][1]["state"] = "API Error"
    failed["job_details"][1]["outputs"] = []
    stub = Stub()

    async def one_failed(request: web.Request) -> web.Response:
        return web.json_response(failed)

    stub._status = one_failed  # type: ignore[method-assign]
    with pytest.raises(DafterError) as caught:
        run(stub, [ASHA, RAVI])
    assert caught.value.code is ErrorCode.PROVIDER_UNAVAILABLE
    assert caught.value.details == (f"at '/files/{RAVI.name}': not transcribed",)

    whole = vector("status-running.response.json")
    whole["job_state"] = "Failed"
    stub = Stub()

    async def job_failed(request: web.Request) -> web.Response:
        return web.json_response(whole)

    stub._status = job_failed  # type: ignore[method-assign]
    with pytest.raises(DafterError) as caught:
        run(stub, [ASHA])
    assert caught.value.code is ErrorCode.PROVIDER_UNAVAILABLE


def test_a_job_that_never_finishes_times_out() -> None:
    ticks = iter(range(0, 10_000, 300))
    stub = Stub(status_replies=["status-running.response.json"])
    with pytest.raises(DafterError) as caught:
        run(stub, [ASHA], clock=lambda: float(next(ticks)))
    assert caught.value.code is ErrorCode.PROVIDER_TIMEOUT


def ref(model: str = "saaras:v3", region: str | None = "ap-south-1", **options: Any) -> ProviderRef:
    return ProviderRef(
        provider="sarvam", model=model, region=region, credential_ref=KEY_REF, options=options
    )


def test_the_batch_provider_is_built_from_the_pinned_ref() -> None:
    batch = batch_for(ref(timeoutSeconds=900))
    assert (batch.provider, batch.model) == ("sarvam", "saaras:v3")
    assert build_batch(ref("saaras:v4")).model == "saaras:v4"


@pytest.mark.parametrize(
    ("bad", "code", "pointer"),
    [
        (ref("saaras:v3-realtime"), ErrorCode.UNSUPPORTED_CAPABILITY, "/transcription/batch/model"),
        (ref(region="us-east-1"), ErrorCode.RESIDENCY_VIOLATION, None),
        (ref(pollMs=5), ErrorCode.INVALID_CONFIG, "/transcription/batch/options/pollMs"),
        (ref(timeoutSeconds=5), ErrorCode.INVALID_CONFIG, "/transcription/batch/options"),
        (
            ProviderRef(provider="silero", model="silero"),
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "/transcription/batch/provider",
        ),
        (None, ErrorCode.INVALID_CONFIG, "/transcription/batch"),
    ],
)
def test_a_batch_ref_the_pass_cannot_run_is_refused(
    bad: ProviderRef | None, code: ErrorCode, pointer: str | None
) -> None:
    with pytest.raises(DafterError) as caught:
        batch_for(bad)
    assert caught.value.code is code
    if pointer:
        assert any(pointer in d for d in caught.value.details)


def test_chunks_read_either_spelling_and_refuse_ragged_timestamps() -> None:
    chunks = {"start_time_seconds": [0.5], "end_time_seconds": [1.0]}
    assert chunks_of({"timestamps": {"words": ["हाँ"], **chunks}})[0].text == "हाँ"
    assert chunks_of({"timestamps": {"chunks": ["हाँ"], **chunks}})[0].text == "हाँ"
    assert chunks_of({}) == ()
    with pytest.raises(DafterError):
        chunks_of({"timestamps": {"words": ["a", "b"], **chunks}})
