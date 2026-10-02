from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from dafter_core.enums import AddressingMode, Channel
from dafter_core.hashing import seal
from dafter_runtime import worker
from dafter_runtime.personas import RECORDING_NOTICES, SCRIPTS
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.telephony import CALL_STATUS, answered
from dafter_runtime.worker import room_options, speak_first, stt_sample_rate
from livekit import rtc

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-telephony-job.json"
WEB_JOB = JOB.with_name("hindi-webrtc-job.json")
POOL = "dafter-py"
CALLER = "p_4b81e0d7"
RECORDED, NOT_RECORDED = RECORDING_NOTICES["hi"]


def job() -> bytes:
    return JOB.read_bytes().strip()


def variant(change: Callable[[dict[str, Any]], None], source: Path = JOB) -> Plan:
    doc = json.loads(source.read_bytes())
    change(doc)
    sealed, _ = seal(json.dumps(doc))
    return plan(load(sealed), POOL)


def test_the_pinned_phone_call_answers_every_turn_on_the_narrowband_line() -> None:
    p = plan(load(job()), POOL)
    assert p.config.channel is Channel.TELEPHONY
    assert p.config.agent.addressing.mode is AddressingMode.ALWAYS
    assert not p.called_by_name
    assert p.opening == p.persona.greeting
    assert stt_sample_rate(p) == 8000
    assert p.pipeline.tts is not None and p.pipeline.tts.options["sampleRate"] == 8000
    options = room_options(p, 8000)
    assert options.audio_input is not False and options.close_on_disconnect


def unrecorded(doc: dict[str, Any]) -> None:
    doc["recording"] = {"enabled": False}


def only_when_recorded(doc: dict[str, Any]) -> None:
    doc["telephony"] = {"recordingNotice": "when_recorded"}


def silent_unless_recorded(doc: dict[str, Any]) -> None:
    unrecorded(doc)
    only_when_recorded(doc)


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (lambda doc: None, RECORDED),
        (unrecorded, NOT_RECORDED),
        (only_when_recorded, RECORDED),
        (silent_unless_recorded, None),
        (lambda doc: doc.update(language="en-IN"), RECORDING_NOTICES["en"][0]),
    ],
    ids=["recorded", "not recorded", "when recorded", "silent when not recorded", "english"],
)
def test_the_caller_hears_whether_the_call_is_recorded(
    change: Callable[[dict[str, Any]], None], said: str | None
) -> None:
    doc = json.loads(job())
    change(doc)
    if doc["language"] == "en-IN":
        doc["turn"]["strategy"], doc["turn"]["localVadEnabled"] = "semantic", True
    sealed, _ = seal(json.dumps(doc))
    assert plan(load(sealed), POOL).disclosure == said


def test_a_webrtc_session_says_nothing_about_recording() -> None:
    p = variant(lambda doc: doc.update(recording={"enabled": False}), WEB_JOB)
    assert p.disclosure is None and not p.on_a_phone


def test_every_language_the_worker_speaks_has_its_recording_notice() -> None:
    assert {language for _, language in SCRIPTS} == set(RECORDING_NOTICES)
    for recorded, not_recorded in RECORDING_NOTICES.values():
        assert recorded and not_recorded and recorded != not_recorded


@dataclass
class Line:
    identity: str
    kind: int = rtc.ParticipantKind.PARTICIPANT_KIND_SIP
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class Room:
    remote_participants: dict[str, Line] = field(default_factory=dict)
    handlers: dict[str, list[Callable[..., Any]]] = field(default_factory=dict)

    def on(self, event: str, callback: Callable[..., Any]) -> None:
        self.handlers.setdefault(event, []).append(callback)

    def off(self, event: str, callback: Callable[..., Any]) -> None:
        self.handlers[event].remove(callback)

    def emit(self, event: str, *args: Any) -> None:
        for callback in list(self.handlers.get(event, [])):
            callback(*args)


async def ring_then_answer(room: Room) -> None:
    await asyncio.sleep(0)
    room.remote_participants["p_browser"] = Line(
        "p_browser", rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD, {CALL_STATUS: "active"}
    )
    room.emit("participant_connected", room.remote_participants["p_browser"])
    phone = Line(CALLER, attributes={CALL_STATUS: "dialing"})
    room.remote_participants[CALLER] = phone
    room.emit("participant_connected", phone)
    await asyncio.sleep(0)
    phone.attributes[CALL_STATUS] = "ringing"
    room.emit("participant_attributes_changed", {CALL_STATUS: "ringing"}, phone)
    await asyncio.sleep(0)
    phone.attributes[CALL_STATUS] = "active"
    room.emit("participant_attributes_changed", {CALL_STATUS: "active"}, phone)


def test_the_agent_waits_until_the_phone_is_picked_up() -> None:
    async def scenario() -> tuple[str | None, Room]:
        room = Room()
        caller, _ = await asyncio.gather(answered(room, 1), ring_then_answer(room))
        return caller, room

    caller, room = asyncio.run(scenario())
    assert caller == CALLER
    assert all(not callbacks for callbacks in room.handlers.values())


def test_a_phone_nobody_picks_up_is_given_up() -> None:
    room = Room(remote_participants={CALLER: Line(CALLER, attributes={CALL_STATUS: "ringing"})})
    assert asyncio.run(answered(room, 0.01)) is None


class Session:
    def __init__(self) -> None:
        self.said: list[tuple[str, bool]] = []

    def say(self, text: str, allow_interruptions: bool) -> None:
        self.said.append((text, allow_interruptions))


def test_once_answered_the_notice_comes_first_and_cannot_be_talked_over() -> None:
    p = plan(load(job()), POOL)
    room = Room(remote_participants={CALLER: Line(CALLER, attributes={CALL_STATUS: "active"})})
    session = Session()
    asyncio.run(speak_first(p, session, room))  # type: ignore[arg-type]
    assert session.said == [(RECORDED, False), (p.persona.greeting, True)]


def test_nothing_is_said_to_a_phone_that_never_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    async def never(room: Any, within: float) -> None:
        assert within == 30 + 15
        return None

    monkeypatch.setattr(worker, "answered", never)
    session = Session()
    asyncio.run(speak_first(plan(load(job()), POOL), session, Room()))  # type: ignore[arg-type]
    assert session.said == []


def test_a_webrtc_greeting_waits_for_no_phone() -> None:
    p = variant(lambda doc: doc["agent"].update(greets=True), WEB_JOB)
    session = Session()
    asyncio.run(speak_first(p, session, Room()))  # type: ignore[arg-type]
    assert session.said == [(p.persona.greeting, True)]
