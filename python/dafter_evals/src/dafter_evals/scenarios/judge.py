from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from dafter_core.enums import Stage
from livekit.agents import APIConnectOptions, function_tool, llm

from ..screen.judge import TOOL, VERDICTS, schema
from ..screen.turn import Classify
from .transcript import Tokens

SYSTEM = (
    "You grade one whole phone call between a caller and a voice agent, including the tools "
    "the agent called and what they returned. Call submit_scores once, giving each criterion "
    "pass, fail, or maybe when the call does not show it, and one short reason."
)
PASS = "pass"


@dataclass(frozen=True, slots=True)
class Verdicts:
    verdicts: dict[str, str] = field(default_factory=dict)
    reasoning: str = ""
    error: str | None = None

    @property
    def passed(self) -> bool:
        return self.error is None and all(v == PASS for v in self.verdicts.values())


def criteria_names(expectations: tuple[str, ...]) -> dict[str, str]:
    return {f"c{i + 1}": text for i, text in enumerate(expectations)}


def prompt(criteria: dict[str, str], call: str) -> str:
    lines = [f"- {name}: {text}" for name, text in criteria.items()]
    return "\n".join(["Criteria:", *lines, "", "The call:", call])


async def _accept(raw_arguments: dict[str, object]) -> None:
    return None


def read(arguments: str, criteria: dict[str, str]) -> Verdicts:
    try:
        raw = json.loads(arguments)
    except json.JSONDecodeError:
        return Verdicts(error="the judge sent arguments that are not JSON")
    if not isinstance(raw, dict):
        return Verdicts(error="the judge sent arguments that are not an object")
    verdicts = {name: raw.get(name) for name in criteria}
    bad = sorted(name for name, v in verdicts.items() if v not in VERDICTS)
    if bad:
        return Verdicts(error=f"the judge gave no verdict for {', '.join(bad)}")
    return Verdicts(
        verdicts={criteria[k]: str(v) for k, v in verdicts.items()},
        reasoning=str(raw.get("reasoning", "")),
    )


class CallJudge:
    def __init__(self, model: llm.LLM[Any], classify: Classify, timeout: float) -> None:
        self._model = model
        self._classify = classify
        self._options = APIConnectOptions(max_retry=1, timeout=timeout)

    async def grade(self, expectations: tuple[str, ...], call: str, tokens: Tokens) -> Verdicts:
        if not expectations:
            return Verdicts()
        criteria = criteria_names(expectations)
        tool = function_tool(_accept, raw_schema=schema(list(criteria)))
        ctx = llm.ChatContext()
        ctx.add_message(role="system", content=SYSTEM)
        ctx.add_message(role="user", content=prompt(criteria, call))
        try:
            response = await self._model.chat(
                chat_ctx=ctx, tools=[tool], tool_choice="required", conn_options=self._options
            ).collect()
        except Exception as exc:
            return Verdicts(error=str(self._classify(exc, Stage.LLM).code))
        if response.usage is not None:
            tokens.add(response.usage.prompt_tokens, response.usage.completion_tokens)
        calls = [c for c in response.tool_calls if c.name == TOOL]
        if not calls:
            return Verdicts(error="the judge called no scoring tool")
        return read(calls[0].arguments, criteria)
