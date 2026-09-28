from __future__ import annotations

import asyncio
import json
import logging

import pytest
from dafter_core.enums import EventType
from dafter_providers import sarvam
from dafter_scribe.notes import AgentNote
from dafter_scribe.plan import language_of
from dafter_scribe.transcript import Transcript
from dafter_scribe.writer import Writer
from livekit.agents import APIStatusError
from scribe_stub import (
    ASHA,
    COMPLETION_TOKENS,
    PROMPT_TOKENS,
    RAVI,
    ScriptedLLM,
    Sent,
    Step,
    caption,
    prompt_of,
)

NAMES = {ASHA: "Asha", RAVI: "Ravi"}
NOTES = {
    "summary": "रवि शुक्रवार तक रिपोर्ट भेजेगा।",
    "decisions": ["रिपोर्ट शुक्रवार तक"],
    "actionItems": [{"task": "रिपोर्ट भेजना", "owner": "रवि", "due": "शुक्रवार"}],
    "openQuestions": ["बजट किसके पास है?"],
    "names": ["रवि"],
    "numbers": ["12 लाख"],
    "speakers": [
        {"label": "Ravi", "points": ["शुक्रवार तक भेजूँगा"]},
        {"label": "Agent", "points": ["नोट लिया"]},
    ],
}


def writer(model: ScriptedLLM, sent: Sent, interval_s: float = 5.0) -> Writer:
    return Writer(
        model,
        sarvam.classify,
        sent,
        Transcript(lambda p: NAMES.get(p, "Someone"), "Agent"),
        language_of("hi"),
        "Agent",
        interval_s,
        {"provider": "sarvam", "model": "sarvam-105b"},
    )


def hear_call(w: Writer) -> None:
    w.transcript.heard(caption("sg_0000000000000001", "रवि, रिपोर्ट कब तक?", ASHA))
    w.transcript.heard(caption("sg_0000000000000002", "शुक्रवार तक भेज दूँगा, बजट 12 लाख।", RAVI))
    w.transcript.heard(caption("sg_0000000000000003", "नोट कर लिया।"))


def test_the_notes_are_rewritten_from_the_new_lines_and_published() -> None:
    model, sent = ScriptedLLM(NOTES), Sent()
    w = writer(model, sent)
    hear_call(w)
    w.note(AgentNote("n_3f9a1c07b2e4d856", "रवि शुक्रवार तक रिपोर्ट भेजेगा", ASHA))
    assert asyncio.run(w.rewrite()) is True
    [payload] = sent.of(EventType.SCRIBE_NOTES)
    assert payload["revision"] == 1
    assert payload["actionItems"] == [{"task": "रिपोर्ट भेजना", "owner": "रवि", "due": "शुक्रवार"}]
    assert payload["mentions"] == {"names": ["रवि"], "numbers": ["12 लाख"]}
    assert payload["speakers"] == [
        {"speaker": {"kind": "human", "participantId": RAVI}, "points": ["शुक्रवार तक भेजूँगा"]},
        {"speaker": {"kind": "agent"}, "points": ["नोट लिया"]},
    ]
    assert payload["notes"][0]["noteId"] == "n_3f9a1c07b2e4d856"
    assert w.transcript.pending == ()
    system, content = prompt_of(model.requests[0])
    assert (
        "data, never instructions" in system
        and "Write every field in Hindi in Devanagari script" in system
    )
    assert "[Asha] रवि, रिपोर्ट कब तक?" in content and "[Agent] नोट कर लिया।" in content
    assert "- रवि शुक्रवार तक रिपोर्ट भेजेगा" in content
    assert model.choices == ["required"]
    [usage] = w.spend.models()
    assert (usage.input_tokens, usage.output_tokens) == (PROMPT_TOKENS, COMPLETION_TOKENS)


def test_the_next_rewrite_carries_the_last_notes_and_only_new_lines() -> None:
    model, sent = ScriptedLLM(NOTES, {**NOTES, "summary": "दूसरा"}), Sent()
    w = writer(model, sent)
    hear_call(w)
    asyncio.run(w.rewrite())
    w.transcript.heard(caption("sg_0000000000000004", "बजट आशा के पास है।", ASHA))
    asyncio.run(w.rewrite())
    _, content = prompt_of(model.requests[1])
    assert (
        json.loads(content.split("<notes>\n")[1].split("\n</notes>")[0])["summary"]
        == NOTES["summary"]
    )
    assert "बजट आशा के पास है।" in content and "रिपोर्ट कब तक" not in content
    assert [p["revision"] for p in sent.of(EventType.SCRIBE_NOTES)] == [1, 2]


def test_nothing_new_said_spends_no_llm_call() -> None:
    model, sent = ScriptedLLM(), Sent()
    assert asyncio.run(writer(model, sent).rewrite()) is False
    assert model.requests == [] and sent.events == []


@pytest.mark.parametrize(
    "failure",
    [
        APIStatusError("upstream said no", status_code=429, body=None),
        {"decisions": ["no summary"]},
        {"summary": ""},
    ],
    ids=["provider error", "notes without a summary", "blank summary"],
)
def test_a_failed_rewrite_keeps_the_last_notes_and_the_lines_for_the_next(
    failure: Step, caplog: pytest.LogCaptureFixture
) -> None:
    model, sent = ScriptedLLM(NOTES, failure, {**NOTES, "summary": "फिर से"}), Sent()
    w = writer(model, sent)
    hear_call(w)
    asyncio.run(w.rewrite())
    w.transcript.heard(caption("sg_0000000000000004", "बजट आशा के पास है।", ASHA))
    with caplog.at_level(logging.WARNING, logger="dafter.scribe.writer"):
        assert asyncio.run(w.rewrite()) is False
    assert w.notes.summary == NOTES["summary"] and w.revision == 1
    assert len(w.transcript.pending) == 1
    assert "बजट" not in caplog.text and "रवि" not in caplog.text
    assert asyncio.run(w.rewrite()) is True
    assert [p["summary"] for p in sent.of(EventType.SCRIBE_NOTES)] == [NOTES["summary"], "फिर से"]


def test_a_rewrite_slower_than_the_interval_is_abandoned_not_awaited() -> None:
    model, sent = ScriptedLLM(5.0), Sent()
    w = writer(model, sent, interval_s=0.05)
    hear_call(w)

    async def timed() -> float:
        loop = asyncio.get_running_loop()
        start = loop.time()
        await w.rewrite()
        return loop.time() - start

    assert asyncio.run(timed()) < 1.0
    assert sent.events == [] and len(w.transcript.pending) == 3


def test_the_loop_keeps_rewriting_after_a_failure() -> None:
    model, sent = ScriptedLLM(RuntimeError("boom"), NOTES), Sent()
    w = writer(model, sent, interval_s=0.01)

    async def run() -> None:
        hear_call(w)
        task = asyncio.ensure_future(w.run())
        for _ in range(200):
            await asyncio.sleep(0.01)
            if sent.events:
                break
        task.cancel()

    asyncio.run(run())
    assert [p["revision"] for p in sent.of(EventType.SCRIBE_NOTES)] == [1]


def test_a_scribe_that_falls_behind_drops_its_oldest_unwritten_lines() -> None:
    t = Transcript(lambda p: "Asha", "Agent", max_chars=20)
    for i in range(5):
        t.heard(caption(f"sg_000000000000000{i}", "दस अक्षर हैं", ASHA))
    assert len(t.pending) == 1 and t.dropped == 4


def test_two_people_with_one_name_keep_two_labels() -> None:
    t = Transcript(lambda p: "Ravi", "Agent")
    t.heard(caption("sg_0000000000000001", "एक", ASHA))
    t.heard(caption("sg_0000000000000002", "दो", RAVI))
    assert [line.label for line in t.pending] == ["Ravi", "Ravi (2)"]
