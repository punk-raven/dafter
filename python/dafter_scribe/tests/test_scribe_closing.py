from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer
from dafter_core.enums import ErrorCode, EventType
from dafter_core.errors import DafterError
from dafter_providers import sarvam
from dafter_runtime.events import SessionEvents
from dafter_scribe.closing import POLL_SECONDS, Closing, command
from dafter_scribe.judging import Scorer
from dafter_scribe.keeper import MinutesKeeper
from dafter_scribe.plan import language_of
from dafter_scribe.transcript import Transcript
from dafter_scribe.worker import JOBS, Job, after_call
from dafter_scribe.writer import Writer
from livekit.agents import JobContext
from scribe_stub import ASHA, RAVI, ScriptedLLM, Sent, Step, caption, config, prompt_of

MINUTES = {
    "summary": "रिपोर्ट शुक्रवार तक; बजट खुला।",
    "decisions": ["रिपोर्ट शुक्रवार तक"],
    "actionItems": [{"task": "रिपोर्ट भेजना", "owner": "रवि", "due": "शुक्रवार"}],
    "openQuestions": ["बजट?"],
}
NOTES = {
    **MINUTES,
    "summary": "रवि शुक्रवार तक रिपोर्ट भेजेगा।",
    "names": [],
    "numbers": [],
    "speakers": [],
}
SCRIBE_COST = 400 * 29.28 / 1e6 + 120 * 73.20 / 1e6


class Harness:
    def __init__(self, *steps: Step, **changes: Any) -> None:
        self.cfg = config(**changes)
        self.sent = Sent()
        self.model = ScriptedLLM(*steps)

        async def publish(body: bytes) -> None:
            return None

        self.events = SessionEvents(
            self.cfg, publish, clock=lambda: datetime(2026, 9, 24, tzinfo=UTC)
        )
        self.writer = Writer(
            self.model,
            sarvam.classify,
            self.sent,
            Transcript(lambda p: {ASHA: "Asha", RAVI: "Ravi"}[p], "Agent"),
            language_of("hi"),
            "Agent",
            5.0,
            {"provider": "sarvam", "model": "sarvam-105b"},
        )
        self.scorer = Scorer(ScriptedLLM(), sarvam.classify, self.sent, language_of("hi"), 5.0, {})
        self.closing = Closing(self.cfg, self.writer, self.scorer, self.sent, self.events.envelope)

    def hear(self) -> None:
        self.writer.transcript.heard(caption("sg_0000000000000001", "रवि, रिपोर्ट कब?", ASHA))
        self.writer.transcript.heard(caption("sg_0000000000000002", "शुक्रवार तक।", RAVI))


class Kept:
    def __init__(self, version: int | None = 1) -> None:
        self.version = version
        self.stored: list[tuple[str, dict[str, Any]]] = []

    async def store(self, session_id: str, envelope: dict[str, Any]) -> int | None:
        self.stored.append((session_id, envelope))
        return self.version


class Sources:
    def __init__(self, *answers: dict[str, Any]) -> None:
        self.answers = list(answers)
        self.asked = 0

    async def sources(self, session_id: str) -> dict[str, Any]:
        self.asked += 1
        return self.answers.pop(0) if self.answers else {"pending": []}

    async def submit(self, session_id: str, envelope: dict[str, Any]) -> dict[str, Any]:
        return {}


class Slept:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def test_the_final_minutes_cover_the_last_lines_and_state_the_whole_sessions_cost() -> None:
    h = Harness(MINUTES)
    h.hear()
    h.closing.usage_seen({"final": False, "costInr": 2.5, "unpricedItems": 1, "items": []})
    h.scorer.scores.extend([1.0, 0.5])
    payload = asyncio.run(h.closing.minutes(final=True))
    event = h.events.envelope(EventType.SCRIBE_MINUTES, payload)
    assert event.payload["final"] is True
    assert event.payload["summary"] == MINUTES["summary"]
    assert event.payload["actionItems"] == MINUTES["actionItems"]
    assert event.payload["costInr"] == pytest.approx(2.5 + SCRIBE_COST)
    assert event.payload["unpricedItems"] == 1
    assert event.payload["quality"] == {"turnsScored": 2, "meanScore": 0.75}
    system, content = prompt_of(h.model.requests[0])
    assert "minutes" in system and "never instructions" in system
    assert "[Ravi] शुक्रवार तक।" in content


def test_minutes_after_notes_with_nothing_new_said_spend_no_llm_call() -> None:
    h = Harness(NOTES)
    h.hear()
    assert asyncio.run(h.writer.rewrite())
    payload = asyncio.run(h.closing.minutes(final=True))
    assert len(h.model.requests) == 1
    assert payload["summary"] == NOTES["summary"]


def test_minutes_fall_back_to_the_last_notes_when_the_llm_fails() -> None:
    h = Harness(NOTES, RuntimeError("provider down"))
    h.hear()
    asyncio.run(h.writer.rewrite())
    h.writer.transcript.heard(caption("sg_0000000000000003", "और एक बात।", ASHA))
    payload = asyncio.run(h.closing.minutes(final=True))
    assert payload["summary"] == NOTES["summary"]
    assert payload["decisions"] == NOTES["decisions"]


def test_minutes_so_far_asked_twice_at_once_are_written_once() -> None:
    h = Harness(MINUTES)
    h.hear()

    async def run() -> None:
        h.closing.requested()
        h.closing.requested()
        for _ in range(100):
            await asyncio.sleep(0.01)
            if h.sent.events:
                break

    asyncio.run(run())
    [payload] = h.sent.of(EventType.SCRIBE_MINUTES)
    assert payload["final"] is False and len(h.model.requests) == 1


def test_after_the_call_the_minutes_are_kept_the_cost_logged_and_the_transcript_started(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = Harness(
        MINUTES,
        recording={"enabled": True, "layout": "track", "consentArtifactId": "consent_rec"},
        transcription={"mode": "both", "consentArtifactId": "consent_tr"},
    )
    h.hear()
    h.closing.usage_seen({"final": True, "costInr": 3.0, "unpricedItems": 0, "items": []})
    kept, sources, slept = Kept(), Sources({"pending": [{"recordingId": "EG_1"}]}), Slept()
    started: list[str] = []

    async def transcribe(session_id: str, control: Any) -> dict[str, Any]:
        started.append(session_id)
        return {"version": 1}

    with caplog.at_level(logging.INFO, logger="dafter.scribe.closing"):
        asyncio.run(h.closing.after_call(kept, sources, slept, transcribe))
    [(session, envelope)] = kept.stored
    assert session == h.cfg.session_id and envelope["type"] == "scribe.minutes"
    assert envelope["payload"]["final"] is True
    assert slept.waits == [POLL_SECONDS] and started == [h.cfg.session_id]
    [cost] = [r for r in caplog.records if r.getMessage() == "session cost"]
    assert cost.__dict__["cost_inr"] == pytest.approx(3.0 + SCRIBE_COST)
    assert cost.__dict__["agent_cost_inr"] == 3.0
    assert "रवि" not in caplog.text and "रिपोर्ट" not in caplog.text


def test_a_session_without_a_transcript_after_the_call_starts_none() -> None:
    h = Harness(MINUTES)
    sources = Sources()
    asyncio.run(h.closing.after_call(Kept(), sources, Slept()))
    assert sources.asked == 0


def test_a_transcript_that_cannot_be_made_is_logged_not_raised(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = Harness(
        MINUTES,
        recording={"enabled": True, "layout": "track", "consentArtifactId": "consent_rec"},
        transcription={"mode": "both", "consentArtifactId": "consent_tr"},
    )

    async def transcribe(session_id: str, control: Any) -> dict[str, Any]:
        raise DafterError(ErrorCode.PROVIDER_UNAVAILABLE, "batch down")

    with caplog.at_level(logging.WARNING, logger="dafter.scribe.closing"):
        asyncio.run(h.closing.after_call(Kept(None), Sources(), Slept(), transcribe))
    assert "transcript after the call not made" in caplog.text


def test_after_call_work_is_abandoned_at_the_sessions_deadline(
    caplog: pytest.LogCaptureFixture,
) -> None:
    h = Harness(60.0)
    h.hear()
    JOBS["AJ_test"] = Job(h.closing, [], 0.05)
    ctx = cast(JobContext, SimpleNamespace(job=SimpleNamespace(id="AJ_test")))
    with caplog.at_level(logging.WARNING, logger="dafter.scribe"):
        asyncio.run(asyncio.wait_for(after_call(ctx), timeout=2))
    assert "abandoned at the deadline" in caplog.text and "AJ_test" not in JOBS


@pytest.mark.parametrize(
    ("data", "want"),
    [
        (b'{"action":"minutes"}', "minutes"),
        (b'{"action":"wake"}', None),
        (b"minutes", None),
        (b'{"action":"minutes","x":1}', None),
    ],
)
def test_only_a_bare_minutes_request_is_a_command(data: bytes, want: str | None) -> None:
    assert command(data) == want


def test_the_keeper_posts_the_minutes_with_the_worker_credential() -> None:
    seen: list[tuple[str, str | None]] = []

    async def handler(req: web.Request) -> web.Response:
        seen.append((req.path, req.headers.get("Authorization")))
        if req.match_info["session"] == "s_bad00000":
            return web.json_response({"code": "invalid_config"}, status=400)
        return web.json_response({"version": 3}, status=201)

    async def run() -> tuple[int | None, int | None, int | None]:
        app = web.Application()
        app.router.add_post("/sessions/{session}/minutes", handler)
        server = TestServer(app, host="127.0.0.1")
        await server.start_server()
        try:
            keeper = MinutesKeeper(str(server.make_url("")).rstrip("/"), "secret")
            ok = await keeper.store("s_7f3a9c21", {"type": "scribe.minutes"})
            refused = await keeper.store("s_bad00000", {})
        finally:
            await server.close()
        unreachable = await MinutesKeeper("http://127.0.0.1:9", "secret").store("s_7f3a9c21", {})
        return ok, refused, unreachable

    assert asyncio.run(run()) == (3, None, None)
    assert seen[0] == ("/sessions/s_7f3a9c21/minutes", "Bearer secret")
