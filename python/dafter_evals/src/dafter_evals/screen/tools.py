from __future__ import annotations

import json
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from livekit.agents import function_tool, llm

if TYPE_CHECKING:
    from .bank import Question

WEATHER = {"temperature_c": 31, "conditions": "light rain"}
ENDED = "the call ends after this reply"


async def _weather(raw_arguments: dict[str, object]) -> str:
    return json.dumps(WEATHER)


async def _end_call(raw_arguments: dict[str, object]) -> str:
    return ENDED


TOOLS: tuple[llm.Tool, ...] = (
    function_tool(
        _weather,
        raw_schema={
            "name": "get_weather",
            "description": "Look up today's weather for one Indian city.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string", "description": "the city's name"}},
                "required": ["city"],
            },
        },
    ),
    function_tool(
        _end_call,
        raw_schema={
            "name": "end_call",
            "description": "End the phone call when the caller asks to hang up.",
            "parameters": {"type": "object", "properties": {}},
        },
    ),
)
NAMES = frozenset({"get_weather", "end_call"})


@dataclass(frozen=True, slots=True)
class ToolCheck:
    missed: list[str]
    unexpected: list[str]
    parallel: bool
    quoted: bool | None
    claimed_without_call: bool | None


def check(question: Question, responses: Sequence[Sequence[str]], reply: str | None) -> ToolCheck:
    called = Counter(name for response in responses for name in response)
    expected = Counter(question.tools)
    text = reply or ""
    missed = sorted((expected - called).elements())
    quoted = None
    if question.quote and called & expected:
        quoted = any(term in text for term in question.quote)
    claimed = None
    if question.tools:
        claimed = bool(missed) and any(term in text for term in question.claims)
    return ToolCheck(
        missed=missed,
        unexpected=sorted((called - expected).elements()),
        parallel=any(len(response) > 1 for response in responses),
        quoted=quoted,
        claimed_without_call=claimed,
    )
