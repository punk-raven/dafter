from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dafter_core.enums import Stage
from livekit.agents import APIConnectOptions, llm

from ..screen.turn import Classify
from .task import Task
from .transcript import AGENT_ROLE, LANGUAGE_NAMES, Line, Tokens

STOP = "###STOP###"
SILENT_AGENT = "(the agent says nothing)"


def instructions(task: Task) -> str:
    facts = "\n".join(f"- {k}: {v}" for k, v in task.user.facts.items()) or "- none"
    language = LANGUAGE_NAMES[task.language]
    return "\n".join(
        [
            "You play a caller on a phone call with a voice agent. Stay in character; never "
            "play the agent and never describe what you do.",
            f"Who you are: {task.user.persona}",
            f"What you want from the agent: {task.user.goal}",
            "Facts you give only when the agent asks for them:",
            facts,
            f"Speak {language}, code-mixed with English words the way Indian callers talk. Say "
            "one short spoken turn at a time, with no quotes, labels or stage directions.",
            "Answer a question the agent asks you plainly, including yes or no when it asks "
            "whether to go ahead, as your goal says.",
            f"When your goal is met, when the agent says it cannot help further, or when the "
            f"call ends, reply with exactly {STOP} and nothing else.",
        ]
    )


@dataclass(frozen=True, slots=True)
class Said:
    text: str | None
    error: str | None = None

    @property
    def stopped(self) -> bool:
        return self.text is None


def context(task: Task, lines: list[Line]) -> llm.ChatContext:
    ctx = llm.ChatContext()
    ctx.add_message(role="system", content=instructions(task))
    for line in lines:
        if line.role == AGENT_ROLE:
            ctx.add_message(role="user", content=line.text or SILENT_AGENT)
        else:
            ctx.add_message(role="assistant", content=line.text)
    return ctx


class Simulator:
    def __init__(
        self, model: llm.LLM[Any], classify: Classify, task: Task, tokens: Tokens, timeout: float
    ) -> None:
        self._model = model
        self._classify = classify
        self._task = task
        self._tokens = tokens
        self._options = APIConnectOptions(max_retry=1, timeout=timeout)

    async def next(self, lines: list[Line]) -> Said:
        try:
            response = await self._model.chat(
                chat_ctx=context(self._task, lines), conn_options=self._options
            ).collect()
        except Exception as exc:
            return Said(None, error=str(self._classify(exc, Stage.LLM).code))
        if response.usage is not None:
            self._tokens.add(response.usage.prompt_tokens, response.usage.completion_tokens)
        text = response.text.strip()
        if not text or STOP in text:
            return Said(None)
        return Said(text)
