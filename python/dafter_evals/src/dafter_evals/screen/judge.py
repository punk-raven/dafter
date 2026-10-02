from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from dafter_core.enums import Stage
from livekit.agents import APIConnectOptions, function_tool, llm
from livekit.agents.evals import tool_use_judge

from .bank import Bank
from .turn import Classify

VERDICTS = frozenset({"pass", "fail", "maybe"})
POINTS = {"pass": 1.0, "maybe": 0.5, "fail": 0.0}
TOOL = "submit_scores"
MARKDOWN = re.compile(r"\*\*|__|`|^\s*#|^\s*[-*•]\s|^\s*\d+[.)]\s", re.MULTILINE)
DIGITS = re.compile(r"\d")
SYSTEM = (
    "You grade one reply from a voice agent on a live phone call. Call submit_scores once, "
    "giving each criterion pass, fail, or maybe when you cannot tell, and one short reason."
)
SPEAKABLE = (
    "the reply can be read aloud as it stands: one or two short sentences, no markdown, lists, "
    "headings, emojis or symbols that cannot be spoken, and numbers written as words."
)


@dataclass(frozen=True, slots=True)
class Scores:
    verdicts: dict[str, str] = field(default_factory=dict)
    reasoning: str = ""
    error: str | None = None

    def score(self) -> float | None:
        if not self.verdicts:
            return None
        return sum(POINTS[v] for v in self.verdicts.values()) / len(self.verdicts)


def markdown(text: str) -> bool:
    return MARKDOWN.search(text) is not None


def digits(text: str) -> bool:
    return DIGITS.search(text) is not None


def criteria(bank: Bank) -> dict[str, str]:
    written = f", written in {bank.script} script" if bank.script else ""
    found = {
        "correctness": "the reply answers the caller, and every fact it states is true.",
        "language": f"the reply is in {bank.name}{written}.",
    }
    if bank.register:
        found["register"] = (
            f"the reply addresses the caller respectfully with {bank.register}, "
            "never with a familiar form."
        )
    found["speakability"] = SPEAKABLE
    return found


def schema(names: list[str]) -> dict[str, Any]:
    verdict = {"type": "string", "enum": sorted(VERDICTS)}
    return {
        "name": TOOL,
        "description": "Submit a verdict for every criterion and one short reason.",
        "parameters": {
            "type": "object",
            "properties": {
                **{n: verdict for n in names},
                "reasoning": {"type": "string"},
            },
            "required": [*names, "reasoning"],
            "additionalProperties": False,
        },
    }


async def _accept(raw_arguments: dict[str, object]) -> None:
    return None


class Judge:
    def __init__(self, model: llm.LLM[Any], classify: Classify, bank: Bank, timeout: float):
        self.model = model
        self._classify = classify
        self._criteria = criteria(bank)
        self._tool = function_tool(_accept, raw_schema=schema(list(self._criteria)))
        self._options = APIConnectOptions(max_retry=0, timeout=timeout)

    def prompt(self, question: str, reply: str) -> str:
        lines = [f"- {name}: {text}" for name, text in self._criteria.items()]
        return "\n".join(["Criteria:", *lines, "", f"Caller: {question}", f"Reply: {reply}"])

    async def score(self, question: str, reply: str) -> Scores:
        ctx = llm.ChatContext()
        ctx.add_message(role="system", content=SYSTEM)
        ctx.add_message(role="user", content=self.prompt(question, reply))
        try:
            response = await self.model.chat(
                chat_ctx=ctx, tools=[self._tool], tool_choice="required", conn_options=self._options
            ).collect()
        except Exception as exc:
            return Scores(error=str(self._classify(exc, Stage.LLM).code))
        calls = [c for c in response.tool_calls if c.name == TOOL]
        if not calls:
            return Scores(error="the judge called no scoring tool")
        return self._read(calls[0].arguments)

    def _read(self, arguments: str) -> Scores:
        try:
            raw = json.loads(arguments)
        except json.JSONDecodeError:
            return Scores(error="the judge sent arguments that are not JSON")
        if not isinstance(raw, dict):
            return Scores(error="the judge sent arguments that are not an object")
        verdicts = {name: raw.get(name) for name in self._criteria}
        bad = sorted(name for name, v in verdicts.items() if v not in VERDICTS)
        if bad:
            return Scores(error=f"the judge gave no verdict for {', '.join(bad)}")
        return Scores(
            verdicts={k: str(v) for k, v in verdicts.items()},
            reasoning=str(raw.get("reasoning", "")),
        )

    async def tool_use(self, history: llm.ChatContext) -> str:
        try:
            judged = await tool_use_judge().evaluate(chat_ctx=history, llm=self.model)
        except Exception as exc:
            return f"error: {self._classify(exc, Stage.LLM).code}"
        return judged.verdict
