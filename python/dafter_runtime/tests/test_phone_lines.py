from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from dafter_core.hashing import seal
from dafter_runtime.personas import RECORDING_NOTICES
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.telephony import CALL_STATUS, PhoneLines, Relink, answered
from dafter_runtime.worker import speak_first
from livekit import rtc

JOBS = Path(__file__).resolve().parents[3] / "testdata" / "agent"
POOL = "dafter-py"
CALLER, SECOND = "p_4b81e0d7", "p_7c20d9e1"
BROWSER = "p_9c2e11aa"
RECORDED = RECORDING_NOTICES["hi"][0]
SIP = rtc.ParticipantKind.PARTICIPANT_KIND_SIP
STANDARD = rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD


def job(name: str, change: Callable[[dict[str, Any]], None] = lambda doc: None) -> Plan:
    doc = json.loads((JOBS / name).read_bytes())
    change(doc)
    sealed, _ = seal(json.dumps(doc))
    return plan(load(sealed), POOL)


def phone_call() -> Plan:
    return job("hindi-telephony-job.json")


def meeting(doc: dict[str, Any]) -> None:
    doc["telephony"] = {"trunk": "vobiz", "phoneGuests": "dial_out"}
    doc["recording"] = {"enabled": True, "layout": "track", "consentArtifactId": "consent_rec"}
    doc["agent"]["greets"] = True


@dataclass
class Line:
    identity: str
    kind: int = SIP
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class Local:
    identity: str = "agent-AJ_7f3a9c21"


@dataclass
class Room:
    remote_participants: dict[str, Line] = field(default_factory=dict)
    handlers: dict[str, list[Callable[..., Any]]] = field(default_factory=dict)
    local_participant: Local = field(default_factory=Local)

    def on(self, event: str, callback: Callable[..., Any]) -> None:
        self.handlers.setdefault(event, []).append(callback)

    def off(self, event: str, callback: Callable[..., Any]) -> None:
        self.handlers[event].remove(callback)

    def emit(self, event: str, *args: Any) -> None:
        for callback in list(self.handlers.get(event, [])):
            callback(*args)

    def join(self, line: Line) -> Line:
        self.remote_participants[line.identity] = line
        self.emit("participant_connected", line)
        return line

    def status(self, line: Line, status: str) -> None:
        line.attributes[CALL_STATUS] = status
        self.emit("participant_attributes_changed", {CALL_STATUS: status}, line)

    def leave(self, identity: str) -> None:
        self.emit("participant_disconnected", self.remote_participants.pop(identity))


class Session:
    def __init__(self) -> None:
        self.said: list[tuple[str, bool]] = []

    def say(self, text: str, allow_interruptions: bool) -> None:
        self.said.append((text, allow_interruptions))


async def ring(room: Room, identity: str, answer: bool = True) -> None:
    phone = room.join(Line(identity, attributes={CALL_STATUS: "dialing"}))
    await asyncio.sleep(0)
    room.status(phone, "ringing")
    await asyncio.sleep(0)
    if answer:
        room.status(phone, "active")


def test_the_agent_waits_until_that_phone_is_picked_up() -> None:
    async def scenario() -> tuple[bool, Room]:
        room = Room()
        room.join(Line(BROWSER, STANDARD, {CALL_STATUS: "active"}))
        phone = room.join(Line(CALLER, attributes={CALL_STATUS: "dialing"}))

        async def pick_up() -> None:
            await asyncio.sleep(0)
            room.status(phone, "ringing")
            await asyncio.sleep(0)
            room.status(phone, "active")

        got, _ = await asyncio.gather(answered(room, CALLER, 1), pick_up())
        return got, room

    got, room = asyncio.run(scenario())
    assert got
    assert all(not callbacks for callbacks in room.handlers.values())


def test_a_phone_nobody_picks_up_or_that_hangs_up_ringing_is_given_up() -> None:
    room = Room(remote_participants={CALLER: Line(CALLER, attributes={CALL_STATUS: "ringing"})})
    assert asyncio.run(answered(room, CALLER, 0.01)) is False

    async def hung_up() -> bool:
        waiting = asyncio.ensure_future(answered(room, CALLER, 30))
        await asyncio.sleep(0)
        room.leave(CALLER)
        return await waiting

    assert asyncio.run(hung_up()) is False


def test_the_first_caller_hears_the_notice_then_the_greeting_and_a_second_only_the_notice() -> None:
    p = phone_call()

    async def scenario() -> Session:
        room, session = Room(), Session()
        lines = speak_first(p, session, room)  # type: ignore[arg-type]
        assert lines is not None and session.said == []
        await ring(room, CALLER)
        await lines.settled()
        room.leave(CALLER)
        await ring(room, SECOND)
        await lines.settled()
        return session

    said = asyncio.run(scenario()).said
    assert said == [(RECORDED, False), (p.persona.greeting, True), (RECORDED, False)]


def test_a_phone_already_on_the_line_when_the_agent_starts_is_told() -> None:
    p = phone_call()
    room = Room(remote_participants={CALLER: Line(CALLER, attributes={CALL_STATUS: "active"})})

    async def scenario() -> Session:
        session = Session()
        lines = speak_first(p, session, room)  # type: ignore[arg-type]
        assert lines is not None
        await lines.settled()
        return session

    assert asyncio.run(scenario()).said == [(RECORDED, False), (p.persona.greeting, True)]


def test_a_meeting_greets_at_once_and_tells_each_phone_guest_only_that_it_is_recorded() -> None:
    p = job("hindi-webrtc-job.json", meeting)
    assert p.called_by_name

    async def scenario() -> Session:
        room, session = Room(), Session()
        room.join(Line(BROWSER, STANDARD))
        lines = speak_first(p, session, room)  # type: ignore[arg-type]
        assert lines is not None
        await ring(room, CALLER)
        await ring(room, SECOND)
        await lines.settled()
        return session

    said = asyncio.run(scenario()).said
    assert said == [(p.persona.greeting, True), (RECORDED, False), (RECORDED, False)]


def test_the_answer_wait_counts_from_each_phones_own_join_not_the_job_start() -> None:
    async def scenario() -> list[str]:
        room, told = Room(), list[str]()
        lines = PhoneLines(room, 0.05, told.append)
        lines.listen()
        await asyncio.sleep(0.15)
        await ring(room, CALLER)
        await ring(room, SECOND, answer=False)
        room.join(Line(BROWSER, STANDARD, {CALL_STATUS: "active"}))
        await lines.settled()
        return told

    assert asyncio.run(scenario()) == [CALLER]


def test_a_session_that_takes_no_phone_watches_for_none() -> None:
    p = job("hindi-webrtc-job.json")
    room, session = Room(), Session()
    assert speak_first(p, session, room) is None  # type: ignore[arg-type]
    assert not room.handlers and session.said == []


@dataclass
class RoomIO:
    linked: Line | None = None
    linked_to: list[str] = field(default_factory=list)

    @property
    def linked_participant(self) -> Line | None:
        return self.linked

    def set_participant(self, participant_identity: str | None) -> None:
        assert participant_identity is not None
        self.linked_to.append(participant_identity)

    def attach(self, room: Room, first: Line) -> None:
        self.linked = first

        def left(line: Line) -> None:
            if self.linked is not None and line.identity == self.linked.identity:
                self.linked = None

        room.on("participant_disconnected", left)


def test_an_always_session_links_the_next_caller_after_the_first_hangs_up() -> None:
    room, io = Room(), RoomIO()
    io.attach(room, room.join(Line(CALLER)))
    Relink(room, io).listen()
    room.leave(CALLER)
    assert io.linked_to == []
    room.join(Line("agent-AJ_other", rtc.ParticipantKind.PARTICIPANT_KIND_AGENT))
    assert io.linked_to == []
    room.join(Line(SECOND))
    assert io.linked_to == [SECOND]


def test_an_always_session_moves_to_someone_still_present_and_ignores_others_leaving() -> None:
    room, io = Room(), RoomIO()
    room.join(Line(BROWSER, STANDARD))
    io.attach(room, room.join(Line(CALLER)))
    room.join(Line(SECOND))
    Relink(room, io).listen()
    room.leave(SECOND)
    assert io.linked_to == []
    room.leave(CALLER)
    assert io.linked_to == [BROWSER]
