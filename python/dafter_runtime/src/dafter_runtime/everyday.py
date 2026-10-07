from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from livekit.agents import StopResponse

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
        raise StopResponse()

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


STAY_SILENT = "stay_silent"


def stay_silent() -> Tool:
    async def run(arguments: dict[str, Any]) -> str:
        raise StopResponse()

    return Tool(
        name=STAY_SILENT,
        description=(
            "Say nothing to the latest line, because it was not meant for you: the people in "
            "the call are talking to each other, chatting, or answering someone else. Call it "
            "on its own and do not write any words."
        ),
        speed=Speed.FAST,
        effect=Effect.READ,
        run=run,
    )


def switch_language(languages: list[str], ask: Callable[[str], bool]) -> Tool:
    async def run(arguments: dict[str, Any]) -> str:
        language = str(arguments.get("language", ""))
        if not ask(language):
            return f"Not switched: speak one of {', '.join(languages)}."
        return f"Switched. Reply only in {language} from now on, until you are asked for another."

    return Tool(
        name="switch_language",
        description=(
            "Switch the language you reply in when the person talking to you asks for "
            "another one, for example 'English mein bolo'. The language is one of the "
            "listed BCP 47 tags, whose first part is the ISO 639 language code."
        ),
        speed=Speed.FAST,
        effect=Effect.READ,
        run=run,
        parameters={
            "type": "object",
            "properties": {"language": {"type": "string", "enum": languages}},
            "required": ["language"],
        },
    )


__all__ = [
    "STAY_SILENT",
    "current_time",
    "go_quiet",
    "stay_silent",
    "switch_language",
    "who_is_here",
]
