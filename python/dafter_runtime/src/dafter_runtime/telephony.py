from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from livekit import rtc

CALL_STATUS = "sip.callStatus"
ANSWERED = "active"
ANSWER_GRACE_SECONDS = 15
EVENTS = ("participant_connected", "participant_attributes_changed")


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


def picked_up(p: Line) -> bool:
    on_a_phone = p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_SIP
    return on_a_phone and p.attributes.get(CALL_STATUS) == ANSWERED


def on_the_line(room: Phones) -> str | None:
    return next((p.identity for p in room.remote_participants.values() if picked_up(p)), None)


async def answered(room: Phones, within: float) -> str | None:
    loop = asyncio.get_running_loop()
    done: asyncio.Future[str] = loop.create_future()

    def check(*_: Any) -> None:
        identity = on_the_line(room)
        if identity is not None and not done.done():
            done.set_result(identity)

    for event in EVENTS:
        room.on(event, check)
    try:
        check()
        return await asyncio.wait_for(done, within)
    except TimeoutError:
        return None
    finally:
        for event in EVENTS:
            room.off(event, check)


__all__ = ["ANSWER_GRACE_SECONDS", "answered", "on_the_line", "picked_up"]
