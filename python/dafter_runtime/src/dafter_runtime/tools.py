from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Coroutine, Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from dafter_core.enums import Role
from livekit.agents import RunContext, function_tool, llm

from .consent import Confirmations

log = logging.getLogger("dafter.runtime.tools")

NO_PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "required": []}


class Speed(StrEnum):
    FAST = "fast"
    SLOW = "slow"
    ASYNC = "async"


class Effect(StrEnum):
    READ = "read"
    DRAFT = "draft"
    EXTERNAL = "external"
    BINDING = "binding"


CONFIRMED = frozenset({Effect.EXTERNAL, Effect.BINDING})

Run = Callable[[dict[str, Any]], Coroutine[Any, Any, str]]
Deliver = Callable[[str, str], None]


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    speed: Speed
    effect: Effect
    run: Run
    parameters: dict[str, Any] = field(default_factory=lambda: dict(NO_PARAMETERS))
    roles: frozenset[Role] = frozenset()
    filler: str = ""


class ToolRefused(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Filling:
    phrase: Callable[[], str | None]
    after_s: float


NO_FILLING = Filling(lambda: None, 0.0)


def checked(tools: Iterable[Tool], shared_filler: bool = False) -> dict[str, Tool]:
    by_name: dict[str, Tool] = {}
    for tool in tools:
        if tool.name in by_name:
            raise ToolRefused(f"two tools are named {tool.name}")
        if tool.speed is Speed.SLOW and not tool.filler and not shared_filler:
            raise ToolRefused(f"{tool.name} is slow and has no filler to play")
        if tool.effect is Effect.BINDING and not tool.roles:
            raise ToolRefused(f"{tool.name} is binding and names no role allowed to use it")
        by_name[tool.name] = tool
    return by_name


def arguments_key(arguments: dict[str, Any]) -> str:
    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


ASK = (
    "Not done yet. Ask the person talking to you, in one short question, whether they want "
    "this done, and call this tool again with the same arguments only after they say yes."
)
NOBODY = "Not done: nobody is talking to you, so nobody can confirm it."
ROLE = "Not done: the person talking to you is not allowed to do this."
STARTED = "Started. The result will follow when it is ready; tell the person it is on its way."


class Registry:
    def __init__(
        self,
        tools: Iterable[Tool],
        caller: Callable[[], str | None],
        role_of: Callable[[str], Role | None],
        confirmations: Confirmations,
        deliver: Deliver,
        filling: Filling = NO_FILLING,
        shared_filler: bool = False,
    ) -> None:
        self._tools = checked(tools, shared_filler)
        self._filling = filling
        self._caller = caller
        self._role_of = role_of
        self._confirmations = confirmations
        self._deliver = deliver
        self._one_at_a_time = asyncio.Lock()
        self._background: set[asyncio.Task[None]] = set()

    @property
    def names(self) -> list[str]:
        return list(self._tools)

    def heard(self, speaker: str, text: str) -> None:
        self._confirmations.heard(speaker, text)

    def refusal(self, tool: Tool, arguments: dict[str, Any]) -> str | None:
        if tool.effect not in CONFIRMED:
            return None
        caller = self._caller()
        if caller is None:
            return NOBODY
        if tool.effect is Effect.BINDING and self._role_of(caller) not in tool.roles:
            return ROLE
        key = arguments_key(arguments)
        if self._confirmations.take(caller, tool.name, key):
            return None
        self._confirmations.ask(caller, tool.name, key)
        return ASK

    async def call(
        self, name: str, arguments: dict[str, Any], context: RunContext[Any] | None
    ) -> str:
        tool = self._tools[name]
        async with self._one_at_a_time:
            refused = self.refusal(tool, arguments)
            if refused is not None:
                log.info("tool held back", extra={"tool": name, "effect": str(tool.effect)})
                return refused
            log.info("tool called", extra={"tool": name, "speed": str(tool.speed)})
            if tool.speed is Speed.ASYNC:
                self._start(tool, arguments)
                return STARTED
            if tool.speed is Speed.SLOW and context is not None:
                if phrase := tool.filler or self._filling.phrase():
                    async with context.with_filler(phrase, delay=self._filling.after_s):
                        return await tool.run(arguments)
            return await tool.run(arguments)

    def _start(self, tool: Tool, arguments: dict[str, Any]) -> None:
        async def finish() -> None:
            self._deliver(tool.name, await tool.run(arguments))

        task = asyncio.ensure_future(finish())
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def function_tools(self) -> list[llm.Tool | llm.Toolset]:
        return [self._function_tool(tool) for tool in self._tools.values()]

    def _function_tool(self, tool: Tool) -> llm.Tool:
        name = tool.name

        async def invoke(raw_arguments: dict[str, object], context: RunContext[Any]) -> str:
            return await self.call(name, dict(raw_arguments), context)

        schema = {"name": name, "description": tool.description, "parameters": tool.parameters}
        return function_tool(invoke, raw_schema=schema)


__all__ = [
    "NO_FILLING",
    "Effect",
    "Filling",
    "Registry",
    "Speed",
    "Tool",
    "ToolRefused",
    "arguments_key",
    "checked",
]
