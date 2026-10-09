from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from dafter_core.switching import LanguageSwitching
from dafter_runtime.answering import Roster
from dafter_runtime.consent import Confirmations
from dafter_runtime.everyday import current_time, switch_language, who_is_here
from dafter_runtime.naming import words
from dafter_runtime.personas import persona_for
from dafter_runtime.switching import Switching
from dafter_runtime.tools import Effect, Registry, Speed, Tool
from livekit.agents import llm

from ..screen.tools import TOOLS as SCREEN_TOOLS
from .task import CALLER, Task

FIRST_TICKET = 4101
NOT_FOUND = "No order with that id exists in the order system."


def same_value(left: str, right: str) -> bool:
    return comparable(left) == comparable(right)


def comparable(value: str) -> str:
    return "".join(c for c in value.casefold() if c.isalnum())


@dataclass
class State:
    language: str
    effects: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    tickets: list[str] = field(default_factory=list)

    def ticket(self) -> str:
        number = f"TKT-{FIRST_TICKET + len(self.tickets)}"
        self.tickets.append(number)
        return number


def _order(task: Task, order_id: str) -> dict[str, str] | None:
    for known, order in task.world.orders.items():
        if same_value(known, order_id):
            return {
                "order_id": known,
                "status": order.status,
                "item": order.item,
                "delivery": order.delivery,
            }
    return None


def lookup_order(task: Task) -> Tool:
    async def run(arguments: dict[str, Any]) -> str:
        found = _order(task, str(arguments.get("order_id", "")))
        return NOT_FOUND if found is None else json.dumps(found, ensure_ascii=False)

    return Tool(
        name="lookup_order",
        description="Look up one order's status, item and delivery date by its order id.",
        speed=Speed.FAST,
        effect=Effect.READ,
        run=run,
        parameters={
            "type": "object",
            "properties": {"order_id": {"type": "string", "description": "e.g. ORD-4821"}},
            "required": ["order_id"],
        },
    )


def create_ticket(state: State) -> Tool:
    async def run(arguments: dict[str, Any]) -> str:
        state.effects.append(("create_ticket", dict(arguments)))
        return f"Ticket {state.ticket()} raised for order {arguments.get('order_id', '')}."

    return Tool(
        name="create_ticket",
        description=(
            "Raise a complaint ticket with the delivery team for one order. The caller is "
            "told the ticket number."
        ),
        speed=Speed.FAST,
        effect=Effect.EXTERNAL,
        run=run,
        parameters={
            "type": "object",
            "properties": {
                "order_id": {"type": "string"},
                "issue": {"type": "string", "description": "the problem, in one line"},
            },
            "required": ["order_id", "issue"],
        },
    )


def schedule_callback(state: State) -> Tool:
    async def run(arguments: dict[str, Any]) -> str:
        state.effects.append(("schedule_callback", dict(arguments)))
        return f"Callback booked for {arguments.get('when', '')}."

    return Tool(
        name="schedule_callback",
        description="Book a callback from a human agent at a time the caller picks.",
        speed=Speed.FAST,
        effect=Effect.EXTERNAL,
        run=run,
        parameters={
            "type": "object",
            "properties": {
                "when": {"type": "string", "description": "date and time, e.g. 2026-10-09 17:00"}
            },
            "required": ["when"],
        },
    )


def roster_for(task: Task) -> Roster:
    roster = Roster()
    if not task.world.people:
        roster.join(CALLER)
    for person in task.world.people:
        roster.join(person.id, person.name)
    return roster


def switching_for(task: Task) -> Switching:
    tags = task.switchable or (task.language,)
    personas = {tag: persona_for(task.persona, tag, task.agent_name) for tag in tags}
    config = LanguageSwitching(enabled=bool(task.switchable), languages=task.switchable)
    return Switching(config, task.language, personas)


@dataclass
class WorldTools:
    state: State
    roster: Roster
    switching: Switching
    registry: Registry
    extra: list[llm.Tool]

    def tools(self) -> list[llm.Tool | llm.Toolset]:
        return [*self.registry.function_tools(), *self.extra]


def build_world(task: Task, caller: Callable[[], str | None]) -> WorldTools:
    state = State(language=task.language)
    roster = roster_for(task)
    switching = switching_for(task)
    switching.follow_with(lambda tag: setattr(state, "language", tag))
    clock = datetime.fromisoformat(task.world.clock)
    made = {
        "lookup_order": lambda: lookup_order(task),
        "create_ticket": lambda: create_ticket(state),
        "schedule_callback": lambda: schedule_callback(state),
        "current_time": lambda: current_time(lambda: clock),
        "who_is_here": lambda: who_is_here(roster.present),
    }
    tools = [made[name]() for name in task.tools if name in made]
    if switching.enabled:
        tools.append(switch_language(switching.languages, switching.ask))
    name_words = frozenset(words(task.agent_name))
    registry = Registry(
        tools,
        caller=caller,
        role_of=lambda identity: None,
        confirmations=Confirmations(name_words),
        deliver=lambda tool, result: None,
    )
    offered = set(task.tools)
    extra = [t for t in SCREEN_TOOLS if t.id in offered]
    return WorldTools(
        state=state, roster=roster, switching=switching, registry=registry, extra=extra
    )
