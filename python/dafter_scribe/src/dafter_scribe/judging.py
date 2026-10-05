from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from typing import Any

from dafter_core.enums import ErrorCode, EventType
from dafter_evals.screen import bank
from dafter_evals.screen.judge import Judge, Scores
from livekit.agents import llm

from .plan import Language
from .transcript import Line
from .writer import Classify, Emit

log = logging.getLogger("dafter.scribe.judging")

HEARD_LINES = 4
BACKLOG = 8
CODES = frozenset(str(c) for c in ErrorCode)


@dataclass(frozen=True, slots=True)
class Turn:
    segment: str
    question: str
    reply: str


def judged(language: Language) -> bank.Bank:
    base = language.tag.split("-", 1)[0].lower()
    register = bank.load(base).register if base in bank.LANGUAGES else None
    return bank.Bank(language.tag, language.name, language.script, register, ())


class Scorer:
    def __init__(
        self,
        model: llm.LLM[Any],
        classify: Classify,
        emit: Emit,
        language: Language,
        timeout_s: float,
        source: dict[str, str],
    ) -> None:
        self._judge = Judge(model, classify, judged(language), timeout_s)
        self._timeout_s = timeout_s
        self._emit = emit
        self._source = source
        self._heard: deque[str] = deque(maxlen=HEARD_LINES)
        self._turns: deque[Turn] = deque(maxlen=BACKLOG)
        self._ready = asyncio.Event()
        self.scores: list[float] = []

    def heard(self, line: Line) -> None:
        if not line.from_agent:
            self._heard.append(line.text)
            return
        if not self._heard or not line.segment:
            return
        if len(self._turns) == self._turns.maxlen:
            log.warning("judge fell behind, the oldest unscored turn is dropped")
        self._turns.append(Turn(line.segment, "\n".join(self._heard), line.text))
        self._heard.clear()
        self._ready.set()

    async def score(self, turn: Turn) -> dict[str, Any]:
        try:
            scores = await asyncio.wait_for(
                self._judge.score(turn.question, turn.reply), timeout=self._timeout_s
            )
        except TimeoutError:
            scores = Scores(error=str(ErrorCode.PROVIDER_TIMEOUT))
        payload: dict[str, Any] = {"segmentId": turn.segment, "source": self._source}
        value = scores.score()
        if scores.error is not None or value is None:
            payload["error"] = scores.error if scores.error in CODES else str(ErrorCode.INTERNAL)
            log.warning("turn not scored", extra={"code": payload["error"]})
        else:
            payload["score"] = round(value, 4)
            payload["criteria"] = dict(scores.verdicts)
            self.scores.append(value)
        self._emit(EventType.AGENT_TURN_SCORED, payload)
        return payload

    async def run(self) -> None:
        while True:
            await self._ready.wait()
            while self._turns:
                await self.score(self._turns.popleft())
            self._ready.clear()

    def quality(self) -> dict[str, Any]:
        quality: dict[str, Any] = {"turnsScored": len(self.scores)}
        if self.scores:
            quality["meanScore"] = round(sum(self.scores) / len(self.scores), 4)
        return quality


__all__ = ["Scorer", "Turn", "judged"]
