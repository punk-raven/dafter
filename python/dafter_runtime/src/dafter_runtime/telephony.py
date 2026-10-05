from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from livekit import rtc

from .listeners import is_human
from .plan import Plan

CALL_STATUS = "sip.callStatus"
ANSWERED = "active"
ANSWER_GRACE_SECONDS = 15
WATCHED = ("participant_attributes_changed", "participant_disconnected")

log = logging.getLogger("dafter.runtime.telephony")


class Line(Protocol):
    @property
    def identity(self) -> str: ...

    @property
    def kind(self) -> Any: ...

    @property
    def attributes(self) -> Mapping[str, str]: ...


class Phones(Protocol):
    @property
    def remote_participants(self) -> Mapping[str, Line]: ...

    def on(self, event: Any, callback: Callable[..., Any]) -> Any: ...

    def off(self, event: Any, callback: Callable[..., Any]) -> None: ...


class Speaker(Protocol):
    def say(self, text: str, *, allow_interruptions: bool) -> Any: ...


class Linking(Protocol):
    @property
    def linked_participant(self) -> Any: ...

    def set_participant(self, participant_identity: str | None) -> None: ...


def on_a_phone(p: Line) -> bool:
    return bool(p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP)


def picked_up(p: Line) -> bool:
    return on_a_phone(p) and p.attributes.get(CALL_STATUS) == ANSWERED


async def answered(room: Phones, identity: str, within: float) -> bool:
    loop = asyncio.get_running_loop()
    done: asyncio.Future[bool] = loop.create_future()

    def check(*_: Any) -> None:
        if done.done():
            return
        line = room.remote_participants.get(identity)
        if line is None:
            done.set_result(False)
        elif picked_up(line):
            done.set_result(True)

    for event in WATCHED:
        room.on(event, check)
    try:
        check()
        return await asyncio.wait_for(done, within)
    except TimeoutError:
        return False
    finally:
        for event in WATCHED:
            room.off(event, check)


class PhoneLines:
    def __init__(self, room: Phones, within: float, on_answer: Callable[[str], None]) -> None:
        self._room = room
        self._within = within
        self._on_answer = on_answer
        self._seen: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    def listen(self) -> None:
        self._room.on("participant_connected", self.joined)
        for line in list(self._room.remote_participants.values()):
            self.joined(line)

    def joined(self, line: Line) -> None:
        if not on_a_phone(line) or line.identity in self._seen:
            return
        self._seen.add(line.identity)
        task = asyncio.ensure_future(self._wait(line.identity))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _wait(self, identity: str) -> None:
        if await answered(self._room, identity, self._within):
            log.info("phone answered", extra={"participant": identity})
            self._on_answer(identity)
        else:
            log.info("phone not answered", extra={"participant": identity})

    async def settled(self) -> None:
        while self._tasks:
            await asyncio.gather(*self._tasks)


def announcer(p: Plan, speaker: Speaker) -> Callable[[str], None]:
    greeting = p.opening if p.on_a_phone else None

    def announce(identity: str) -> None:
        nonlocal greeting
        if p.disclosure is not None:
            speaker.say(p.disclosure, allow_interruptions=False)
        if greeting is not None:
            speaker.say(greeting, allow_interruptions=True)
            greeting = None

    return announce


def phone_lines(p: Plan, room: Phones, speaker: Speaker) -> PhoneLines | None:
    if not p.takes_phone_calls:
        return None
    within = p.config.telephony.ringing_timeout_seconds + ANSWER_GRACE_SECONDS
    lines = PhoneLines(room, within, announcer(p, speaker))
    lines.listen()
    return lines


class Relink:
    def __init__(self, room: Any, io: Linking) -> None:
        self._room = room
        self._io = io

    def listen(self) -> None:
        self._room.on("participant_connected", self.joined)
        self._room.on("participant_disconnected", self.left)

    def joined(self, line: Any) -> None:
        if self._io.linked_participant is None and is_human(line, self._room):
            self._link(line.identity)

    def left(self, line: Any) -> None:
        if self._io.linked_participant is not None:
            return
        people = [p for p in self._room.remote_participants.values() if is_human(p, self._room)]
        if people:
            self._link(people[0].identity)

    def _link(self, identity: str) -> None:
        log.info("linked to the next person", extra={"participant": identity})
        self._io.set_participant(identity)


__all__ = [
    "ANSWER_GRACE_SECONDS",
    "PhoneLines",
    "Relink",
    "announcer",
    "answered",
    "on_a_phone",
    "phone_lines",
    "picked_up",
]
