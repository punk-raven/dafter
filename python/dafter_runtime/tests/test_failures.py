from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest
from dafter_providers.sarvam.realtime import FinalFirstSTT
from dafter_runtime.events import SessionEvents
from dafter_runtime.metrics import WorkerMetrics
from dafter_runtime.plan import load, plan
from dafter_runtime.worker import watch
from livekit.agents import AgentSession, APIStatusError, llm, stt, tts
from livekit.agents.voice.events import ErrorEvent
from opentelemetry import trace
from prometheus_client import CollectorRegistry
from test_endpoint import Session

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"


def logged_stage(source: object, error: Any) -> tuple[str, str | None]:
    p = plan(load(JOB.read_bytes().strip()), "dafter-py")

    async def publish(body: bytes) -> None:
        return None

    events = SessionEvents(p.config, publish, clock=lambda: datetime(2026, 9, 28, tzinfo=UTC))
    session = Session(None)
    watch(
        cast(AgentSession[Any], session),
        p,
        events,
        trace.NoOpTracer(),
        WorkerMetrics(CollectorRegistry()),
    )
    records: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    capture = Capture()
    logger = logging.getLogger("dafter.runtime")
    logger.addHandler(capture)
    try:
        session.handlers["error"](ErrorEvent(error=error, source=source))
    finally:
        logger.removeHandler(capture)
    [record] = [r for r in records if r.getMessage() == "pipeline stage failed"]
    return record.__dict__["stage"], record.__dict__["native"]


def timeout() -> APIStatusError:
    return APIStatusError("inactivity timeout", status_code=408)


def test_a_sarvam_speech_to_text_failure_is_logged_as_the_stt_stage() -> None:
    source = FinalFirstSTT.__new__(FinalFirstSTT)
    error = stt.STTError(timestamp=0.0, label="sarvam", error=timeout(), recoverable=False)
    assert logged_stage(source, error) == ("stt", "408")


@pytest.mark.parametrize(
    ("kind", "stage"),
    [(stt.STT, "stt"), (llm.LLM, "llm"), (tts.TTS, "tts")],
)
def test_each_stage_is_told_by_what_failed_not_where_it_is_defined(kind: type, stage: str) -> None:
    assert logged_stage(Mock(spec=kind), timeout())[0] == stage


def test_anything_else_is_the_control_stage() -> None:
    assert logged_stage(object(), timeout())[0] == "control"
