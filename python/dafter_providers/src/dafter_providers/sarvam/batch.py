from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

import aiohttp
from dafter_core.config import ProviderRef
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError, ProviderContext

from .. import credentials
from ..batch import AudioFile, Chunk, FileTranscript, Rendering
from ..options import Options
from .languages import language_code

NAME = "sarvam"
BASE_URL = "https://api.sarvam.ai"
JOB_PATH = "/speech-to-text/job/v1"
KEY_HEADER = "api-subscription-key"
CREDENTIAL = "SARVAM_API_KEY"
BLOB_HEADERS = {"x-ms-blob-type": "BlockBlob"}
BATCH_MODELS = frozenset({"saaras:v3", "saaras:v4"})
REGIONS = frozenset({"ap-south-1"})
MODES: Mapping[Rendering, str] = {"verbatim": "verbatim", "clean": "transcribe"}
MAX_FILES_PER_JOB = 20
POINTER = "/transcription/batch"
POLL_SECONDS = 5.0
TIMEOUT_SECONDS = (60, 21600)
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=120)

Sleep = Callable[[float], Awaitable[None]]
Clock = Callable[[], float]


def _error(code: ErrorCode, message: str, native: str | None = None, *details: str) -> DafterError:
    return DafterError(
        code,
        message,
        stage=Stage.STT,
        provider=ProviderContext(NAME, native_code=native),
        details=details,
    )


def status_error(status: int, what: str) -> DafterError:
    code = ErrorCode.PROVIDER_UNAVAILABLE
    if status in (401, 403):
        code = ErrorCode.AUTHENTICATION_FAILED
    elif status == 429:
        code = ErrorCode.QUOTA_EXCEEDED
    elif status in (400, 404, 422):
        code = ErrorCode.INVALID_CONFIG
    return _error(code, f"{NAME} batch refused to {what} (http {status})", str(status))


def chunks_of(output: Mapping[str, Any]) -> tuple[Chunk, ...]:
    stamps = output.get("timestamps") or {}
    texts = stamps.get("words") or stamps.get("chunks") or []
    starts = stamps.get("start_time_seconds") or []
    ends = stamps.get("end_time_seconds") or []
    if not (len(texts) == len(starts) == len(ends)):
        raise _error(ErrorCode.INTERNAL, f"{NAME} batch returned timestamps of unequal lengths")
    return tuple(
        Chunk(text=str(t).strip(), start_s=float(s), end_s=float(e))
        for t, s, e in zip(texts, starts, ends, strict=True)
        if str(t).strip()
    )


def transcript_of(name: str, output: Mapping[str, Any]) -> FileTranscript:
    text = output.get("transcript")
    if not isinstance(text, str):
        raise _error(ErrorCode.INTERNAL, f"{NAME} batch returned an output with no transcript")
    language = output.get("language_code")
    return FileTranscript(
        name=name,
        text=text.strip(),
        language=language if isinstance(language, str) else None,
        chunks=chunks_of(output),
    )


class SarvamBatch:
    def __init__(
        self,
        key: str,
        model: str,
        timeout_s: float,
        base_url: str = BASE_URL,
        sleep: Sleep = asyncio.sleep,
        clock: Clock = time.monotonic,
        poll_s: float = POLL_SECONDS,
    ) -> None:
        self._key = key
        self._model = model
        self._timeout_s = timeout_s
        self._base_url = base_url.rstrip("/")
        self._sleep = sleep
        self._clock = clock
        self._poll_s = poll_s

    @property
    def provider(self) -> str:
        return NAME

    @property
    def model(self) -> str:
        return self._model

    async def transcribe(
        self, files: Sequence[AudioFile], language: str, rendering: Rendering
    ) -> Mapping[str, FileTranscript]:
        out: dict[str, FileTranscript] = {}
        async with aiohttp.ClientSession(timeout=REQUEST_TIMEOUT) as http:
            for start in range(0, len(files), MAX_FILES_PER_JOB):
                batch = files[start : start + MAX_FILES_PER_JOB]
                out.update(await self._job(http, batch, language, rendering))
        return out

    async def _call(
        self, http: aiohttp.ClientSession, method: str, path: str, what: str, body: Any = None
    ) -> dict[str, Any]:
        headers = {KEY_HEADER: self._key}
        try:
            async with http.request(
                method, self._base_url + path, json=body, headers=headers
            ) as resp:
                if resp.status >= 300:
                    raise status_error(resp.status, what)
                doc = await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise _error(
                ErrorCode.PROVIDER_UNAVAILABLE, f"{NAME} batch unreachable to {what}"
            ) from exc
        except TimeoutError as exc:
            raise _error(ErrorCode.PROVIDER_TIMEOUT, f"{NAME} batch timed out to {what}") from exc
        if not isinstance(doc, dict):
            raise _error(ErrorCode.INTERNAL, f"{NAME} batch answered {what} with no JSON object")
        return doc

    async def _transfer(
        self, http: aiohttp.ClientSession, method: str, url: str, what: str, **kw: Any
    ) -> bytes:
        try:
            async with http.request(method, url, **kw) as resp:
                if resp.status >= 300:
                    raise status_error(resp.status, what)
                return await resp.read()
        except aiohttp.ClientError as exc:
            raise _error(
                ErrorCode.PROVIDER_UNAVAILABLE, f"{NAME} batch storage failed to {what}"
            ) from exc

    async def _job(
        self,
        http: aiohttp.ClientSession,
        files: Sequence[AudioFile],
        language: str,
        rendering: Rendering,
    ) -> dict[str, FileTranscript]:
        created = await self._call(
            http,
            "POST",
            JOB_PATH,
            "create a job",
            {
                "job_parameters": {
                    "language_code": language_code(language, Stage.STT),
                    "model": self._model,
                    "mode": MODES[rendering],
                    "with_timestamps": True,
                    "with_diarization": False,
                }
            },
        )
        job_id = str(created.get("job_id") or "")
        if not job_id:
            raise _error(ErrorCode.INTERNAL, f"{NAME} batch created a job without an id")
        links = await self._call(
            http,
            "POST",
            JOB_PATH + "/upload-files",
            "hand out upload links",
            {"job_id": job_id, "files": [f.name for f in files]},
        )
        uploads = links.get("upload_urls") or {}
        for f in files:
            url = (uploads.get(f.name) or {}).get("file_url")
            if not url:
                raise _error(ErrorCode.INTERNAL, f"{NAME} batch gave no upload link for a file")
            await self._transfer(
                http,
                "PUT",
                url,
                "upload a file",
                data=f.data,
                headers={**BLOB_HEADERS, "Content-Type": f.content_type},
            )
        await self._call(http, "POST", f"{JOB_PATH}/{job_id}/start", "start the job")
        status = await self._wait(http, job_id)
        outputs = self._outputs(status, files)
        links = await self._call(
            http,
            "POST",
            JOB_PATH + "/download-files",
            "hand out download links",
            {"job_id": job_id, "files": list(outputs.values())},
        )
        downloads = links.get("download_urls") or {}
        out: dict[str, FileTranscript] = {}
        for name, output in outputs.items():
            url = (downloads.get(output) or {}).get("file_url")
            if not url:
                raise _error(
                    ErrorCode.INTERNAL, f"{NAME} batch gave no download link for an output"
                )
            raw = await self._transfer(http, "GET", url, "download an output")
            try:
                doc = json.loads(raw)
            except ValueError as exc:
                raise _error(ErrorCode.INTERNAL, f"{NAME} batch output is not JSON") from exc
            out[name] = transcript_of(name, doc)
        return out

    async def _wait(self, http: aiohttp.ClientSession, job_id: str) -> dict[str, Any]:
        deadline = self._clock() + self._timeout_s
        while True:
            status = await self._call(
                http, "GET", f"{JOB_PATH}/{job_id}/status", "report the job's status"
            )
            state = str(status.get("job_state", "")).lower()
            if state == "completed":
                return status
            if state == "failed":
                raise _error(ErrorCode.PROVIDER_UNAVAILABLE, f"{NAME} batch job failed", "Failed")
            if self._clock() >= deadline:
                raise _error(
                    ErrorCode.PROVIDER_TIMEOUT,
                    f"{NAME} batch job did not finish within {int(self._timeout_s)}s",
                )
            await self._sleep(self._poll_s)

    def _outputs(self, status: Mapping[str, Any], files: Sequence[AudioFile]) -> dict[str, str]:
        outputs: dict[str, str] = {}
        failed: list[str] = []
        for detail in status.get("job_details") or []:
            inputs = [i.get("file_name") for i in detail.get("inputs") or []]
            results = [o.get("file_name") for o in detail.get("outputs") or []]
            if detail.get("state") == "Success" and inputs and results:
                outputs[str(inputs[0])] = str(results[0])
            else:
                failed.extend(str(i) for i in inputs)
        missing = [f.name for f in files if f.name not in outputs]
        if missing:
            raise _error(
                ErrorCode.PROVIDER_UNAVAILABLE,
                f"{NAME} batch did not transcribe {len(missing)} file(s)",
                None,
                *(f"at '/files/{name}': not transcribed" for name in sorted(missing)),
            )
        return outputs


def build_batch(ref: ProviderRef) -> SarvamBatch:
    context = ProviderContext(NAME)
    if ref.model not in BATCH_MODELS:
        raise DafterError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"{NAME} batch is pinned to a model this pass does not run",
            stage=Stage.STT,
            provider=context,
            details=(f"at '{POINTER}/model': one of {', '.join(sorted(BATCH_MODELS))}",),
        )
    if ref.region is not None and ref.region not in REGIONS:
        raise DafterError(
            ErrorCode.RESIDENCY_VIOLATION,
            f"{NAME} does not serve the region the batch pass is pinned to",
            stage=Stage.STT,
            provider=context,
        )
    opts = Options(Stage.STT, NAME, ref.options, ("timeoutSeconds",), base=POINTER)
    timeout_s = opts.get("timeoutSeconds", int, 3600)
    if not TIMEOUT_SECONDS[0] <= timeout_s <= TIMEOUT_SECONDS[1]:
        raise opts.error(
            "an option is out of range",
            f"at '{opts.pointer('timeoutSeconds')}': between {TIMEOUT_SECONDS[0]} and "
            f"{TIMEOUT_SECONDS[1]} seconds",
        )
    key = credentials.resolve(ref, Stage.STT, {CREDENTIAL}, pointer=POINTER)
    return SarvamBatch(key, ref.model or "", float(timeout_s))


__all__ = ["BATCH_MODELS", "MODES", "SarvamBatch", "build_batch", "chunks_of", "transcript_of"]
