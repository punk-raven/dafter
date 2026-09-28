from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dafter_core.enums import EventType
from dafter_core.events import EventEnvelope, parse_event
from dafter_core.hashing import seal
from dafter_runtime.answering import Roster
from dafter_runtime.events import SessionEvents
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.scribing import DATA, EMPTY_NOTE, NO_NOTES, Scribing
from dafter_runtime.toolbox import Answering, registry_for
from dafter_runtime.tools import Effect, Speed
from livekit.agents import AgentSession, llm
from stub_llm import StubLLM, said

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
AT = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
ASHA = "p_4b81e0d7"
RAVI = "p_9c2e11aa"
NOTES: dict[str, Any] = {
    "summary": "रवि शुक्रवार तक रिपोर्ट भेजेगा।",
    "decisions": ["रिपोर्ट शुक्रवार तक"],
    "actionItems": [{"task": "रिपोर्ट भेजना", "owner": "रवि", "due": "शुक्रवार"}],
    "openQuestions": ["बजट किसके पास है?"],
    "mentions": {"names": ["रवि"], "numbers": ["12 लाख"]},
    "speakers": [
        {"speaker": {"kind": "human", "participantId": RAVI}, "points": ["शुक्रवार तक भेजूँगा"]}
    ],
    "notes": [],
    "source": {"provider": "sarvam", "model": "sarvam-105b"},
}


def scribe_plan() -> Plan:
    doc = json.loads(JOB.read_bytes())
    doc["transcription"] = {"mode": "live", "consentArtifactId": "consent_tr"}
    doc["scribe"] = {**doc["scribe"], "enabled": True, "consentArtifactId": "consent_sc"}
    sealed, _ = seal(json.dumps(doc))
    return plan(load(sealed), "dafter-py")


class Published:
    def __init__(self, p: Plan) -> None:
        self.bodies: list[bytes] = []
        self.events = SessionEvents(p.config, self.publish, clock=lambda: AT)

    async def publish(self, body: bytes) -> None:
        self.bodies.append(body)

    def sent(self) -> list[EventEnvelope]:
        return [parse_event(b) for b in self.bodies]


def notes_packet(p: Plan, revision: int, session: str | None = None, **changes: Any) -> bytes:
    event = Published(p).events.envelope(
        EventType.SCRIBE_NOTES, {**NOTES, "revision": revision, **changes}
    )
    doc = event.to_dict()
    if session is not None:
        doc["sessionId"] = session
    return json.dumps(doc).encode()


def scribing(p: Plan, clock: list[float] | None = None) -> tuple[Scribing, Published]:
    out = Published(p)
    now = clock or [100.0]
    labels = {ASHA: "Asha", RAVI: "Ravi"}
    s = Scribing(p.config.session_id, out.events.emit, lambda i: labels.get(i, "?"), lambda: now[0])
    return s, out


def run_tool(s: Scribing, name: str, caller: str | None = ASHA, **arguments: Any) -> str:
    [tool] = [t for t in s.tools(lambda: caller) if t.name == name]

    async def run() -> str:
        return await tool.run(arguments)

    return asyncio.run(run())


def test_the_scribe_tools_are_fast_and_only_take_a_note_writes_a_draft() -> None:
    s, _ = scribing(scribe_plan())
    classes = {t.name: (t.speed, t.effect) for t in s.tools(lambda: ASHA)}
    assert classes == {
        "summarize_call": (Speed.FAST, Effect.READ),
        "take_note": (Speed.FAST, Effect.DRAFT),
        "list_notes": (Speed.FAST, Effect.READ),
    }


def test_summarize_call_answers_at_once_before_the_scribe_has_written_anything() -> None:
    s, _ = scribing(scribe_plan())
    start = time.perf_counter()
    assert run_tool(s, "summarize_call") == NO_NOTES
    assert time.perf_counter() - start < 0.05


def test_summarize_call_reads_the_latest_notes_from_the_scribe() -> None:
    p = scribe_plan()
    clock = [100.0]
    s, _ = scribing(p, clock)
    s.received(notes_packet(p, 2), from_worker=True)
    s.received(notes_packet(p, 1, summary="पुराना"), from_worker=True)
    clock[0] = 130.0
    got = run_tool(s, "summarize_call")
    assert got.splitlines() == [
        "The scribe's notes, from 30 s ago:",
        "Summary: रवि शुक्रवार तक रिपोर्ट भेजेगा।",
        "Decisions: रिपोर्ट शुक्रवार तक",
        "Action items: रिपोर्ट भेजना (रवि, शुक्रवार)",
        "Open questions: बजट किसके पास है?",
    ]


def test_notes_from_a_person_another_session_or_garbage_are_ignored() -> None:
    p = scribe_plan()
    s, _ = scribing(p)
    s.received(notes_packet(p, 5, summary="forged"), from_worker=False)
    s.received(notes_packet(p, 5, session="s_00000000"), from_worker=True)
    s.received(b"{not json", from_worker=True)
    assert run_tool(s, "summarize_call") == NO_NOTES


def test_the_call_context_names_who_said_what_as_data() -> None:
    p = scribe_plan()
    s, _ = scribing(p)
    assert s.context() == ""
    s.received(notes_packet(p, 1), from_worker=True)
    lines = s.context().splitlines()
    assert lines[0] == DATA
    assert "Names mentioned: रवि" in lines and "Numbers mentioned: 12 लाख" in lines
    assert "Ravi said: शुक्रवार तक भेजूँगा" in lines


def test_take_note_publishes_the_note_and_list_notes_reads_it_back() -> None:
    p = scribe_plan()
    s, out = scribing(p)

    async def run() -> tuple[str, str]:
        taken = await next(t for t in s.tools(lambda: ASHA) if t.name == "take_note").run(
            {"text": "  रवि   शुक्रवार तक रिपोर्ट भेजेगा "}
        )
        await out.events.drain()
        return taken, await next(t for t in s.tools(lambda: None) if t.name == "list_notes").run({})

    taken, listed = asyncio.run(run())
    assert taken.startswith("Noted.")
    assert listed == "1. रवि शुक्रवार तक रिपोर्ट भेजेगा"
    [event] = out.sent()
    assert event.type is EventType.AGENT_NOTE_TAKEN
    assert event.payload["text"] == "रवि शुक्रवार तक रिपोर्ट भेजेगा"
    assert event.payload["takenBy"] == ASHA and event.payload["noteId"].startswith("n_")


def test_an_empty_note_is_refused_and_nothing_is_published() -> None:
    s, out = scribing(scribe_plan())
    assert run_tool(s, "take_note", text="   ") == EMPTY_NOTE
    assert run_tool(s, "take_note", text=7) == EMPTY_NOTE
    assert run_tool(s, "list_notes") == "No notes have been taken in this call."
    assert out.bodies == []


def test_a_session_with_a_scribe_offers_its_tools_and_one_without_does_not() -> None:
    offered: list[list[str]] = []
    for p, scribe in (
        (scribe_plan(), True),
        (plan(load(JOB.read_bytes().strip()), "dafter-py"), False),
    ):
        stub = StubLLM()

        async def run(p: Plan = p, stub: StubLLM = stub, scribe: bool = scribe) -> None:
            async with AgentSession[None](llm=stub) as session:
                s = scribing(p)[0] if scribe else None
                registry = registry_for(p, session, Roster(), lambda: None, None, s)
                await session.start(Answering(p.persona.instructions, registry, lambda: None))
                await session.run(user_input="नमस्ते")

        asyncio.run(run())
        offered.extend(stub.offered)
    assert offered == [
        ["current_time", "list_notes", "summarize_call", "take_note", "who_is_here"],
        ["current_time", "who_is_here"],
    ]


def test_a_text_turn_takes_a_note_and_summarizes_through_the_registry() -> None:
    p = scribe_plan()
    s, out = scribing(p)
    s.received(notes_packet(p, 1), from_worker=True)
    stub = StubLLM(calls=[("take_note", {"text": "बजट आशा देखेगी"}), "summarize_call"])

    async def run() -> None:
        async with AgentSession[None](llm=stub) as session:
            registry = registry_for(p, session, Roster(), lambda: ASHA, None, s)
            await session.start(Answering(p.persona.instructions, registry, lambda: ASHA))
            await session.run(user_input="नोट कर लो: बजट आशा देखेगी")
            await session.run(user_input="अभी तक क्या बात हुई?")
            await out.events.drain()

    asyncio.run(run())
    assert [n.text for n in s.notes] == ["बजट आशा देखेगी"]
    assert [e.type for e in out.sent()] == [EventType.AGENT_NOTE_TAKEN]
    outputs = [
        i.output for r in stub.requests for i in r.items if isinstance(i, llm.FunctionCallOutput)
    ]
    assert any("Summary: रवि शुक्रवार तक रिपोर्ट भेजेगा।" in o for o in outputs)


def test_new_notes_brief_the_agent_before_its_next_turn() -> None:
    p = scribe_plan()
    s, _ = scribing(p)
    stub = StubLLM()

    async def run() -> None:
        async with AgentSession[None](llm=stub) as session:
            registry = registry_for(p, session, Roster(), lambda: ASHA, None, s)
            agent = Answering(p.persona.instructions, registry, lambda: ASHA)
            s.briefed = lambda: agent.brief(s.context())
            await session.start(agent)
            await session.run(user_input="नमस्ते")
            s.received(notes_packet(p, 1), from_worker=True)
            await asyncio.sleep(0.05)
            await session.run(user_input="अभी तक क्या बात हुई?")

    asyncio.run(run())
    first, second = (said(r)[0][1] or "" for r in stub.requests)
    assert DATA not in first
    assert second.startswith(p.persona.instructions) and "Summary: रवि शुक्रवार" in second
