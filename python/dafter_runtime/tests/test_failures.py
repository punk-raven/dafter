from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest
from dafter_core.enums import EventType
from dafter_core.events import parse_event
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

    async def fail() -> None:
        session.handlers["error"](ErrorEvent(error=error, source=source))
        await events.drain()

    try:
        asyncio.run(fail())
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


def test_a_failed_llm_request_is_published_so_the_page_can_say_why() -> None:
    p = plan(load(JOB.read_bytes().strip()), "dafter-py")
    sent: list[bytes] = []

    async def publish(body: bytes) -> None:
        sent.append(body)

    events = SessionEvents(p.config, publish, clock=lambda: datetime(2026, 9, 28, tzinfo=UTC))
    session = Session(None)
    watch(
        cast(AgentSession[Any], session),
        p,
        events,
        trace.NoOpTracer(),
        WorkerMetrics(CollectorRegistry()),
    )
    limited = APIStatusError("free-models-per-day", status_code=429, body={"error": {}})
    error = llm.LLMError(timestamp=0.0, label="sarvam", error=limited, recoverable=True)

    async def run() -> None:
        session.handlers["error"](ErrorEvent(error=error, source=Mock(spec=llm.LLM)))
        await events.drain()

    asyncio.run(run())
    [event] = [parse_event(body) for body in sent]
    assert event.type is EventType.PROVIDER_DEGRADED
    assert event.payload["recoverable"] is True
    assert event.payload["error"]["stage"] == "llm"
    assert event.payload["error"]["provider"]["nativeCode"] == "429"
    assert "free-models-per-day" not in json.dumps(event.payload)
