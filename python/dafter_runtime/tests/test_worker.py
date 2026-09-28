from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from dafter_runtime.control import CONTROL_URL_ENV
from dafter_runtime.worker import FRAMEWORK_LOGGER, POOL_ENV, on_request, redact_framework_logs

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"


class Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def framework() -> Iterator[logging.Logger]:
    logger = logging.getLogger(FRAMEWORK_LOGGER)
    saved = list(logger.filters)
    yield logger
    logger.filters[:] = saved


def test_framework_log_records_lose_their_transcript_fields(framework: logging.Logger) -> None:
    redact_framework_logs()
    redact_framework_logs()
    capture = Capture()
    framework.addHandler(capture)
    try:
        framework.warning(
            "skipping user input", extra={"lk.pii.user_input": "नमस्ते", "room": "s_7f3a9c21"}
        )
    finally:
        framework.removeHandler(capture)
    record = capture.records[-1]
    assert "lk.pii.user_input" not in record.__dict__
    assert record.__dict__["room"] == "s_7f3a9c21"
    assert sum(type(f).__name__ == "RedactTranscripts" for f in framework.filters) == 1


class Request:
    def __init__(self, metadata: str) -> None:
        self.job = SimpleNamespace(metadata=metadata)
        self.room = SimpleNamespace(name="s_7f3a9c21")
        self.accepted: dict[str, Any] | None = None

    async def accept(self, **given: Any) -> None:
        self.accepted = given

    async def reject(self) -> None:
        raise AssertionError("the pinned job was refused")


def test_the_agent_joins_under_the_name_its_config_gives_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(CONTROL_URL_ENV, raising=False)
    monkeypatch.delenv(POOL_ENV, raising=False)
    req = Request(JOB.read_text().strip())
    asyncio.run(on_request(req))  # type: ignore[arg-type]
    assert req.accepted == {"name": "Nivya", "attributes": {"dafter.role": "agent"}}
