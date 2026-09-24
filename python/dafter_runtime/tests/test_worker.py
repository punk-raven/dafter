from __future__ import annotations

import logging
from collections.abc import Iterator

import pytest
from dafter_runtime.worker import FRAMEWORK_LOGGER, redact_framework_logs


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
