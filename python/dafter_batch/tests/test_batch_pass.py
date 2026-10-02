from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from dafter_batch.run import transcribe_session
from dafter_batch.transcript import Source
from dafter_core.config import ProviderRef
from dafter_core.enums import ErrorCode, EventType
from dafter_core.errors import DafterError
from dafter_core.events import parse_event
from dafter_core.hashing import hash_document, seal
from dafter_providers import AudioFile, BatchTranscriber, Chunk, FileTranscript, Rendering

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
ASHA = {"kind": "human", "participantId": "p_4b81e0d7"}
RAVI = {"kind": "human", "participantId": "p_9d02c3aa"}
NOW = datetime(2026, 9, 24, 10, 31, 12, 4000, tzinfo=UTC)


def after_call_config(mode: str = "after_call") -> dict[str, Any]:
    doc = json.loads(JOB.read_bytes())
    doc["recording"] = {"enabled": True, "layout": "track", "consentArtifactId": "consent_rec"}
    doc["transcription"] = {
        "mode": mode,
        "consentArtifactId": "consent_tr",
        "batch": {"provider": "sarvam", "model": "saaras:v3", "region": "ap-south-1"},
    }
    if mode == "live":
        del doc["transcription"]["batch"]
        doc["recording"] = {"enabled": False}
    sealed, _ = seal(json.dumps(doc))
    loaded: dict[str, Any] = json.loads(sealed)
    return loaded


def answer(config: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "sessionId": config["sessionId"],
        "configHash": config["configHash"],
        "config": config,
        "sources": [
            {
                "recordingId": "EG_Lw3s8PnV6cYb",
                "trackId": "TR_AMravi",
                "speaker": RAVI,
                "startedAt": "2026-09-24T10:00:02.25Z",
                "stoppedAt": "2026-09-24T10:30:00Z",
                "url": "http://storage/s_7f3a9c21/track-TR_AMravi-1.ogg?X-Amz-Signature=b",
                "expiresAt": "2026-09-24T11:30:00Z",
            },
            {
                "recordingId": "EG_Kx9r2QmT4bZa",
                "trackId": "TR_AMasha",
                "speaker": ASHA,
                "startedAt": "2026-09-24T10:00:01.5Z",
                "stoppedAt": "2026-09-24T10:30:00Z",
                "url": "http://storage/s_7f3a9c21/track-TR_AMasha-1.ogg?X-Amz-Signature=a",
                "expiresAt": "2026-09-24T11:30:00Z",
            },
        ],
        "pending": [],
        "skipped": [{"recordingId": "EG_video", "reason": "not an audio track"}],
        **extra,
    }


class Control:
    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = reply
        self.submitted: list[dict[str, Any]] = []

    async def sources(self, session_id: str) -> dict[str, Any]:
        return self.reply

    async def submit(self, session_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        self.submitted.append(envelope)
        return {
            "version": len(self.submitted),
            "transcriptHash": envelope["payload"]["transcriptHash"],
        }


class Recognizer:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str, Rendering]] = []

    @property
    def provider(self) -> str:
        return "sarvam"

    @property
    def model(self) -> str:
        return "saaras:v3"

    async def transcribe(
        self, files: Sequence[AudioFile], language: str, rendering: Rendering
    ) -> Mapping[str, FileTranscript]:
        self.calls.append(([f.name for f in files], language, rendering))
        clean = rendering == "clean"
        return {
            "EG_Kx9r2QmT4bZa.ogg": FileTranscript(
                "EG_Kx9r2QmT4bZa.ogg",
                "",
                "hi-IN",
                (
                    Chunk("मीटिंग कब है?" if clean else "उम्म मीटिंग कब है", 1.21, 3.48),
                    Chunk("और एजेंडा भी।" if clean else "और एजेंडा भी", 6.0, 7.5),
                ),
            ),
            "EG_Lw3s8PnV6cYb.ogg": FileTranscript(
                "EG_Lw3s8PnV6cYb.ogg",
                "10 बजे है ना?" if clean else "दस बजे है ना",
                "unknown",
                (),
            ),
        }


async def fetched(source: Source) -> AudioFile:
    return AudioFile(source.file_name, b"OggS " + source.recording_id.encode(), "audio/ogg")


def run(control: Control, recognizer: Recognizer) -> dict[str, Any]:
    refs: list[ProviderRef | None] = []

    def transcriber(ref: ProviderRef | None) -> BatchTranscriber:
        refs.append(ref)
        return recognizer

    stored = asyncio.run(
        transcribe_session("s_7f3a9c21", control, transcriber, fetched, clock=lambda: NOW)
    )
    assert refs and refs[0] is not None and refs[0].model == "saaras:v3"
    return stored


def test_each_track_is_transcribed_twice_and_every_line_keeps_its_speaker() -> None:
    config = after_call_config()
    control, recognizer = Control(answer(config)), Recognizer()
    stored = run(control, recognizer)

    assert sorted(r for _, _, r in recognizer.calls) == ["clean", "verbatim"]
    assert all(
        files == ["EG_Lw3s8PnV6cYb.ogg", "EG_Kx9r2QmT4bZa.ogg"] and language == "hi"
        for files, language, _ in recognizer.calls
    )
    [submitted] = control.submitted
    event = parse_event(json.dumps(submitted))
    assert event.type is EventType.TRANSCRIPT_VERSION_CREATED
    assert (event.session_id, event.tenant_id) == (config["sessionId"], config["tenantId"])
    body = event.payload
    assert hash_document(json.dumps(body), "transcriptHash") == body["transcriptHash"]
    assert stored == {"version": 1, "transcriptHash": body["transcriptHash"]}
    assert body["provenance"] == {
        "provider": "sarvam",
        "model": "saaras:v3",
        "configHash": config["configHash"],
        "createdAt": "2026-09-24T10:31:12.004Z",
        "recordings": [
            {
                "recordingId": "EG_Lw3s8PnV6cYb",
                "speaker": RAVI,
                "startedAt": "2026-09-24T10:00:02.25Z",
            },
            {
                "recordingId": "EG_Kx9r2QmT4bZa",
                "speaker": ASHA,
                "startedAt": "2026-09-24T10:00:01.5Z",
            },
        ],
    }
    assert [(line["speaker"], line["startMs"], line["text"]) for line in body["verbatim"]] == [
        (RAVI, 0, "दस बजे है ना"),
        (ASHA, 1210, "उम्म मीटिंग कब है"),
        (ASHA, 6000, "और एजेंडा भी"),
    ]
    assert [line["text"] for line in body["clean"]] == ["10 बजे है ना?", "मीटिंग कब है?", "और एजेंडा भी।"]
    ravi = body["verbatim"][0]
    assert ravi["endMs"] == 1_797_750
    assert "language" not in ravi
    assert body["verbatim"][1]["language"] == "hi-IN"


def test_a_re_run_is_a_new_submission_with_its_own_hash() -> None:
    config = after_call_config()
    control = Control(answer(config))
    first = run(control, Recognizer())
    second = asyncio.run(
        transcribe_session(
            "s_7f3a9c21",
            control,
            lambda ref: Recognizer(),
            fetched,
            clock=lambda: datetime(2026, 9, 25, 9, 0, tzinfo=UTC),
        )
    )
    assert second["version"] == 2 and second["transcriptHash"] != first["transcriptHash"]


@pytest.mark.parametrize(
    ("reply", "code", "detail"),
    [
        (
            answer(
                after_call_config(), pending=[{"recordingId": "EG_x", "reason": "still recording"}]
            ),
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "at '/recordings/EG_x': still recording",
        ),
        (answer(after_call_config(), sources=[]), ErrorCode.INVALID_CONFIG, "at '/recording'"),
        (
            answer(after_call_config(), configHash="0" * 64),
            ErrorCode.INVALID_CONFIG,
            "at '/configHash'",
        ),
        (answer(after_call_config("live")), ErrorCode.INVALID_CONFIG, "at '/transcription/mode'"),
    ],
)
def test_nothing_reaches_the_provider_until_the_session_is_ready(
    reply: dict[str, Any], code: ErrorCode, detail: str
) -> None:
    control, recognizer = Control(reply), Recognizer()
    with pytest.raises(DafterError) as caught:
        asyncio.run(
            transcribe_session("s_7f3a9c21", control, lambda ref: recognizer, fetched, lambda: NOW)
        )
    assert caught.value.code is code
    assert any(d.startswith(detail) for d in caught.value.details)
    assert recognizer.calls == [] and control.submitted == []
