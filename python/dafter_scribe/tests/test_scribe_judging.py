from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from dafter_core.enums import EventType
from dafter_providers import sarvam
from dafter_runtime.events import SessionEvents
from dafter_scribe.judging import BACKLOG, Scorer
from dafter_scribe.plan import language_of
from dafter_scribe.transcript import Transcript
from livekit.agents import APIStatusError
from scribe_stub import ASHA, RAVI, ScriptedLLM, Sent, Step, caption, config, prompt_of

VERDICTS = {
    "correctness": "pass",
    "language": "pass",
    "register": "maybe",
    "speakability": "pass",
    "reasoning": "ok",
}
JUDGE = {"provider": "sarvam", "model": "sarvam-105b"}


def scorer(*steps: Step, timeout_s: float = 5.0) -> tuple[Scorer, Sent, ScriptedLLM]:
    model, sent = ScriptedLLM(*steps), Sent()
    return Scorer(model, sarvam.classify, sent, language_of("hi"), timeout_s, JUDGE), sent, model


def hear(s: Scorer, t: Transcript, *lines: tuple[str, str | None]) -> None:
    for i, (text, who) in enumerate(lines):
        line = t.heard(caption(f"sg_{i:016x}", text, who))
        assert line is not None
        s.heard(line)


def drain(s: Scorer, sent: Sent, scored: int) -> None:
    async def run() -> None:
        task = asyncio.ensure_future(s.run())
        for _ in range(200):
            if len(sent.events) >= scored:
                break
            await asyncio.sleep(0.01)
        task.cancel()

    asyncio.run(run())


def test_each_agent_reply_is_scored_against_what_it_answered() -> None:
    s, sent, model = scorer(VERDICTS)
    t = Transcript(lambda p: "Asha", "Agent")
    hear(s, t, ("बजट कितना है?", ASHA), ("और कब तक?", RAVI), ("बारह लाख, शुक्रवार तक।", None))
    drain(s, sent, 1)
    [payload] = sent.of(EventType.AGENT_TURN_SCORED)
    assert payload == {
        "segmentId": "sg_0000000000000002",
        "source": JUDGE,
        "score": 0.875,
        "criteria": {
            "correctness": "pass",
            "language": "pass",
            "register": "maybe",
            "speakability": "pass",
        },
    }
    _, content = prompt_of(model.requests[0])
    assert "Caller: बजट कितना है?\nऔर कब तक?" in content and "Reply: बारह लाख, शुक्रवार तक।" in content
    assert "आप" in content
    assert s.quality() == {"turnsScored": 1, "meanScore": 0.875}


def test_a_score_is_a_valid_event() -> None:
    s, sent, _ = scorer(VERDICTS)
    hear(s, Transcript(lambda p: "Asha", "Agent"), ("हाँ?", ASHA), ("जी।", None))
    drain(s, sent, 1)

    async def publish(body: bytes) -> None:
        return None

    events = SessionEvents(config(), publish, clock=lambda: datetime(2026, 9, 24, tzinfo=UTC))
    [payload] = sent.of(EventType.AGENT_TURN_SCORED)
    assert events.envelope(EventType.AGENT_TURN_SCORED, payload).payload["score"] == 0.875


def test_a_greeting_nobody_asked_for_is_not_scored() -> None:
    s, sent, model = scorer(VERDICTS)
    hear(s, Transcript(lambda p: "Asha", "Agent"), ("नमस्ते! बताइए।", None))
    drain(s, sent, 0)
    assert sent.events == [] and model.requests == []


def test_a_judge_that_fails_says_why_and_scores_nothing() -> None:
    failures: list[Step] = [
        APIStatusError("down", status_code=503, body=None),
        {"correctness": "pass"},
        {"correctness": "great", "language": "pass", "register": "pass", "speakability": "pass"},
    ]
    s, sent, _ = scorer(*failures)
    t = Transcript(lambda p: "Asha", "Agent")
    for i in range(3):
        hear(s, t, (f"सवाल {i}?", ASHA), (f"जवाब {i}।", None))
    drain(s, sent, 3)
    errors = [p.get("error") for p in sent.of(EventType.AGENT_TURN_SCORED)]
    assert errors == ["provider_unavailable", "internal", "internal"]
    assert all("score" not in p for p in sent.of(EventType.AGENT_TURN_SCORED))
    assert s.quality() == {"turnsScored": 0}


def test_a_judge_slower_than_its_bound_is_a_timeout_not_a_stall() -> None:
    s, sent, _ = scorer(5.0, timeout_s=0.05)
    hear(s, Transcript(lambda p: "Asha", "Agent"), ("हाँ?", ASHA), ("जी।", None))
    drain(s, sent, 1)
    assert [p.get("error") for p in sent.of(EventType.AGENT_TURN_SCORED)] == ["provider_timeout"]


def test_a_judge_that_falls_behind_keeps_only_the_latest_turns() -> None:
    s, _, _ = scorer(VERDICTS)
    t = Transcript(lambda p: "Asha", "Agent")
    for i in range(BACKLOG + 3):
        hear(s, t, (f"सवाल {i}?", ASHA), (f"जवाब {i}।", None))
    assert len(s._turns) == BACKLOG and s._turns[0].reply == "जवाब 3।"
