from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .tools import Effect, Speed, Tool

INDIA = ZoneInfo("Asia/Kolkata")

Now = Callable[[], datetime]


def current_time(now: Now = lambda: datetime.now(INDIA)) -> Tool:
    async def run(arguments: dict[str, Any]) -> str:
        at = now().astimezone(INDIA)
        return f"{at:%A %d %B %Y, %H:%M} India Standard Time ({at.isoformat(timespec='minutes')})"

    return Tool(
        name="current_time",
        description="The current date, weekday and time in India. Use it before saying either.",
        speed=Speed.FAST,
        effect=Effect.READ,
        run=run,
    )


def who_is_here(present: Callable[[], list[str]]) -> Tool:
    async def run(arguments: dict[str, Any]) -> str:
        people = present()
        if not people:
            return "Nobody else is in the call."
        return f"{len(people)} in the call: {', '.join(people)}."

    return Tool(
        name="who_is_here",
        description="Who is in the call with you right now, by name where they gave one.",
        speed=Speed.FAST,
        effect=Effect.READ,
        run=run,
    )


def go_quiet(sleep: Callable[[], None]) -> Tool:
    async def run(arguments: dict[str, Any]) -> str:
        sleep()
        return "You are quiet now and will not hear anyone until you are called by name again."

    return Tool(
        name="go_quiet",
        description=(
            "Stop listening until someone calls you by name again. Use it when the person "
            "talking to you is done or asks you to be quiet."
        ),
        speed=Speed.FAST,
        effect=Effect.READ,
        run=run,
    )


__all__ = ["current_time", "go_quiet", "who_is_here"]
