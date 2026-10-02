from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from dafter_core.enums import Role
from dafter_runtime.answering import Roster
from dafter_runtime.consent import Confirmations, answer
from dafter_runtime.everyday import current_time, go_quiet, who_is_here
from dafter_runtime.tools import (
    ASK,
    NOBODY,
    ROLE,
    STARTED,
    Effect,
    Registry,
    Speed,
    Tool,
    ToolRefused,
    checked,
)
from livekit.agents import Agent, AgentSession, llm
from stub_llm import StubLLM

ASHA = "p_4b81e0d7"
RAVI = "p_9d02c3aa"


def tool(
    name: str = "send_summary",
    effect: Effect = Effect.READ,
    speed: Speed = Speed.FAST,
    result: str = "done",
    **extra: Any,
) -> Tool:
    async def run(arguments: dict[str, Any]) -> str:
        return result

    return Tool(name=name, description="d", speed=speed, effect=effect, run=run, **extra)


class Harness:
    def __init__(self, *tools: Tool, caller: str | None = ASHA, role: Role | None = None) -> None:
        self.caller = caller
        self.delivered: list[tuple[str, str]] = []
        self.registry = Registry(
            tools,
            caller=lambda: self.caller,
            role_of=lambda identity: role,
            confirmations=Confirmations(frozenset({"nivya"})),
            deliver=lambda name, result: self.delivered.append((name, result)),
        )

    def call(self, name: str, **arguments: Any) -> str:
        async def run() -> str:
            return await self.registry.call(name, arguments, None)

        return asyncio.run(run())


@pytest.mark.parametrize(
    ("tools", "because"),
    [
        ([tool(), tool()], "two tools are named"),
        ([tool(speed=Speed.SLOW)], "no filler"),
        ([tool(effect=Effect.BINDING)], "names no role"),
    ],
)
def test_a_tool_that_cannot_be_enforced_is_refused(tools: list[Tool], because: str) -> None:
    with pytest.raises(ToolRefused, match=because):
        checked(tools)


@pytest.mark.parametrize("effect", [Effect.READ, Effect.DRAFT])
def test_read_and_draft_run_at_once(effect: Effect) -> None:
    assert Harness(tool(effect=effect)).call("send_summary") == "done"


def test_external_waits_for_the_caller_to_say_yes() -> None:
    h = Harness(tool(effect=Effect.EXTERNAL))
    assert h.call("send_summary", to="team") == ASK
    assert h.call("send_summary", to="team") == ASK
    h.registry.heard(RAVI, "haan")
    assert h.call("send_summary", to="team") == ASK
    h.registry.heard(ASHA, "what did you say?")
    h.registry.heard(ASHA, "निव्या, हाँ कर दो")
    assert h.call("send_summary", to="everyone") == ASK
    h.registry.heard(ASHA, "yes")
    assert h.call("send_summary", to="everyone") == "done"
    assert h.call("send_summary", to="everyone") == ASK


def test_a_no_withdraws_the_request() -> None:
    h = Harness(tool(effect=Effect.EXTERNAL))
    assert h.call("send_summary") == ASK
    h.registry.heard(ASHA, "नहीं, रुको")
    h.registry.heard(ASHA, "yes")
    assert h.call("send_summary") == ASK


def test_external_with_nobody_talking_is_refused() -> None:
    assert Harness(tool(effect=Effect.EXTERNAL), caller=None).call("send_summary") == NOBODY


def test_binding_needs_an_allowed_role_before_it_asks() -> None:
    binding = tool(effect=Effect.BINDING, roles=frozenset({Role.PRESENTER}))
    assert Harness(binding, role=None).call("send_summary") == ROLE
    assert Harness(binding, role=Role.PARTICIPANT).call("send_summary") == ROLE
    h = Harness(binding, role=Role.PRESENTER)
    assert h.call("send_summary") == ASK
    h.registry.heard(ASHA, "ಹೌದು")
    assert h.call("send_summary") == "done"


def test_tool_calls_run_one_at_a_time() -> None:
    active: list[int] = [0]
    most: list[int] = [0]

    async def run(arguments: dict[str, Any]) -> str:
        active[0] += 1
        most[0] = max(most[0], active[0])
        await asyncio.sleep(0.01)
        active[0] -= 1
        return "done"

    slow = Tool(name="lookup", description="d", speed=Speed.FAST, effect=Effect.READ, run=run)
    h = Harness(slow)

    async def both() -> list[str]:
        return list(
            await asyncio.gather(
                h.registry.call("lookup", {}, None), h.registry.call("lookup", {}, None)
            )
        )

    assert asyncio.run(both()) == ["done", "done"]
    assert most[0] == 1


def test_async_acknowledges_and_delivers_later() -> None:
    h = Harness(tool(speed=Speed.ASYNC, result="the report is ready"))

    async def run() -> str:
        started = await h.registry.call("send_summary", {}, None)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return started

    assert asyncio.run(run()) == STARTED
    assert h.delivered == [("send_summary", "the report is ready")]


@pytest.mark.parametrize(
    ("text", "verdict"),
    [
        ("yes", True),
        ("Nivya, yes please", True),
        ("go ahead", True),
        ("हाँ जी", True),
        ("हो चालेल", True),
        ("ಹೌದು", True),
        ("అవును", True),
        ("no", False),
        ("yes, no wait", False),
        ("नको", False),
        ("ಬೇಡ", False),
        ("వద్దు", False),
        ("what time is it", None),
        ("", None),
    ],
)
def test_answers_to_a_confirmation(text: str, verdict: bool | None) -> None:
    assert answer(text, frozenset({"nivya"})) is verdict


def test_current_time_is_stated_in_india() -> None:
    clock = current_time(lambda: datetime(2026, 9, 27, 6, 30, tzinfo=UTC))
    assert (clock.speed, clock.effect) == (Speed.FAST, Effect.READ)
    said_time = asyncio.run(clock.run({}))
    assert said_time == (
        "Sunday 27 September 2026, 12:00 India Standard Time (2026-09-27T12:00+05:30)"
    )


def test_who_is_here_names_the_people_in_the_call() -> None:
    roster = Roster()
    who = who_is_here(roster.present)
    assert asyncio.run(who.run({})) == "Nobody else is in the call."
    roster.join(ASHA, "Asha")
    roster.join(RAVI)
    assert asyncio.run(who.run({})) == "2 in the call: Asha, Speaker 2."


def test_go_quiet_sends_the_agent_to_sleep() -> None:
    slept: list[bool] = []
    quiet = go_quiet(lambda: slept.append(True))
    assert (quiet.speed, quiet.effect) == (Speed.FAST, Effect.READ)
    asyncio.run(quiet.run({}))
    assert slept == [True]


def test_a_slow_tool_plays_its_filler_while_it_runs() -> None:
    async def run(arguments: dict[str, Any]) -> str:
        await asyncio.sleep(0.2)
        return "found it"

    slow = Tool(
        name="lookup",
        description="d",
        speed=Speed.SLOW,
        effect=Effect.READ,
        run=run,
        filler="एक पल रुकिए।",
        filler_after_s=0,
    )
    h = Harness(slow)
    stub = StubLLM(calls=["lookup"])

    async def session_run() -> list[llm.ChatMessage]:
        async with AgentSession[None](llm=stub) as session:
            await session.start(Agent(instructions="i", tools=h.registry.function_tools()))
            await session.run(user_input="look it up")
            return [m for m in session.history.items if isinstance(m, llm.ChatMessage)]

    messages = asyncio.run(session_run())
    assert stub.offered[0] == ["lookup"]
    assert ("assistant", "एक पल रुकिए।") in [(m.role, m.text_content) for m in messages]
    outputs = [i for i in stub.requests[-1].items if isinstance(i, llm.FunctionCallOutput)]
    assert [(o.name, o.output) for o in outputs] == [("lookup", "found it")]
