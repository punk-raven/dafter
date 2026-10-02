from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime

from dafter_providers import sarvam
from dafter_runtime.captions import Captions
from dafter_runtime.events import SessionEvents
from dafter_runtime.scribing import Scribing
from dafter_scribe.plan import language_of
from dafter_scribe.scribe import Scribe
from dafter_scribe.transcript import Transcript
from dafter_scribe.writer import Writer
from scribe_stub import ASHA, RAVI, ScriptedLLM, config

NOTES = {
    "summary": "रवि शुक्रवार तक रिपोर्ट भेजेगा।",
    "decisions": ["रिपोर्ट शुक्रवार तक"],
    "actionItems": [{"task": "रिपोर्ट भेजना", "owner": "रवि", "due": "शुक्रवार"}],
    "openQuestions": [],
    "names": ["रवि"],
    "numbers": [],
    "speakers": [{"label": "Ravi", "points": ["शुक्रवार तक भेजूँगा"]}],
}
AT = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)


class Wire:
    def __init__(self) -> None:
        self.packets: list[bytes] = []

    async def publish(self, body: bytes) -> None:
        self.packets.append(body)


def test_the_agent_answers_from_notes_the_scribe_wrote_from_the_agents_captions() -> None:
    cfg = config()
    agent_wire, scribe_wire = Wire(), Wire()
    agent_events = SessionEvents(cfg, agent_wire.publish, clock=lambda: AT)
    scribe_events = SessionEvents(cfg, scribe_wire.publish, clock=lambda: AT)
    captions = Captions(agent_events.emit, {"provider": "sarvam", "model": "saaras:v3-realtime"})
    writer = Writer(
        ScriptedLLM(NOTES),
        sarvam.classify,
        scribe_events.emit,
        Transcript(lambda p: {ASHA: "Asha", RAVI: "Ravi"}[p], "Agent"),
        language_of("hi"),
        "Agent",
        5.0,
        {"provider": "sarvam", "model": "sarvam-105b"},
    )
    scribe = Scribe(cfg, writer)
    agent_side = Scribing(cfg.session_id, agent_events.emit, lambda p: {RAVI: "Ravi"}.get(p, "?"))

    async def run() -> str:
        captions.heard(ASHA, "रवि, रिपोर्ट", False, "hi")
        captions.heard(ASHA, "रवि, रिपोर्ट कब तक?", True, "hi")
        captions.heard(RAVI, "शुक्रवार तक भेज दूँगा।", True, "hi")
        await agent_events.drain()
        for packet in agent_wire.packets:
            scribe.received(packet, from_worker=True)
        assert await writer.rewrite()
        await scribe_events.drain()
        for packet in scribe_wire.packets:
            agent_side.received(packet, from_worker=True)
        [summarize] = [t for t in agent_side.tools(lambda: ASHA) if t.name == "summarize_call"]
        start = time.perf_counter()
        answer = await summarize.run({})
        assert time.perf_counter() - start < 0.05
        return answer

    answer = asyncio.run(run())
    assert [line.text for line in writer.transcript.pending] == []
    assert "Summary: रवि शुक्रवार तक रिपोर्ट भेजेगा।" in answer
    assert "Action items: रिपोर्ट भेजना (रवि, शुक्रवार)" in answer
    assert "Ravi said: शुक्रवार तक भेजूँगा" in agent_side.context()
    assert [e for e in scribe_wire.packets if b'"scribe.notes"' in e]
