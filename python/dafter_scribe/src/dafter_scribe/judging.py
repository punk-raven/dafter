from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from dafter_core.enums import ErrorCode, EventType
from dafter_evals.screen import bank
from dafter_evals.screen.judge import Judge, Scores, criteria
from dafter_runtime.personas import base_language
from livekit.agents import llm

from .plan import Language
from .quality import Outcome
from .review import FAIL, AudioReference, FailedTurn
from .scoring import ScoringLoop
from .transcript import Line
from .writer import Classify, Emit

log = logging.getLogger("dafter.scribe.judging")

HEARD_LINES = 4
BACKLOG = 8
CODES = frozenset(str(c) for c in ErrorCode)


@dataclass(frozen=True, slots=True)
class Heard:
    line: Line
    at: datetime


@dataclass(frozen=True, slots=True)
class Turn:
    segment: str
    question: str
    reply: str
    asked: tuple[Heard, ...] = ()


def judged(language: Language) -> bank.Bank:
    base = base_language(language.tag)
    if base in bank.LANGUAGES:
        return bank.load(base)
    return bank.Bank(language.tag, language.name, language.script, None, ())


def audio_of(turn: Turn, consent: str | None) -> AudioReference | None:
    if consent is None or not turn.asked:
        return None
    participants = dict.fromkeys(
        p for h in turn.asked if (p := h.line.speaker.get("participantId")) is not None
    )
    return AudioReference(
        consent_id=consent,
        participant_ids=tuple(participants),
        segment_ids=tuple(h.line.segment for h in turn.asked if h.line.segment),
        heard_from=turn.asked[0].at.isoformat(),
        heard_to=turn.asked[-1].at.isoformat(),
    )


class Scorer:
    def __init__(
        self,
        model: llm.LLM[Any],
        classify: Classify,
        emit: Emit,
        language: Language,
        timeout_s: float,
        source: dict[str, str],
        loop: ScoringLoop,
    ) -> None:
        bank_for_language = judged(language)
        self._judge = Judge(model, classify, bank_for_language, timeout_s)
        self._timeout_s = timeout_s
        self._emit = emit
        self._source = source
        self._loop = loop
        self._quality = loop.quality(list(criteria(bank_for_language)))
        self._heard: deque[Heard] = deque(maxlen=HEARD_LINES)
        self._turns: deque[Turn] = deque(maxlen=BACKLOG)
        self._ready = asyncio.Event()
        self._taken = 0
        self.scores: list[float] = []

    def heard(self, line: Line) -> None:
        if not line.from_agent:
            self._heard.append(Heard(line, self._loop.clock()))
            return
        if not self._heard or not line.segment:
            return
        asked = tuple(self._heard)
        self._heard.clear()
        if not self._loop.sampling.picks(line.segment):
            self._quality.turn(Outcome.UNSAMPLED)
            return
        if self._taken >= self._loop.sampling.cap:
            self._quality.turn(Outcome.CAPPED)
            return
        self._taken += 1
        if len(self._turns) == self._turns.maxlen:
            log.warning("judge fell behind, the oldest unscored turn is dropped")
            self._quality.turn(Outcome.DROPPED)
        question = "\n".join(h.line.text for h in asked)
        self._turns.append(Turn(line.segment, question, line.text, asked))
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
            self._quality.turn(Outcome.ERRORED)
        else:
            payload["score"] = round(value, 4)
            payload["criteria"] = dict(scores.verdicts)
            self.scores.append(value)
            self._quality.scored(scores.verdicts, value)
        self._emit(EventType.AGENT_TURN_SCORED, payload)
        if "score" in payload:
            await self.keep_if_failed(turn, scores, payload["score"])
        return payload

    async def keep_if_failed(self, turn: Turn, scores: Scores, score: float) -> bool:
        queue = self._loop.queue
        if queue is None or FAIL not in scores.verdicts.values():
            return False
        failed = FailedTurn(
            session_id=self._loop.session.session_id,
            language=self._loop.session.language,
            channel=self._loop.session.channel,
            segment_id=turn.segment,
            question=turn.question,
            reply=turn.reply,
            criteria=dict(scores.verdicts),
            score=score,
            reasoning=scores.reasoning,
            judged_at=self._loop.clock().isoformat(),
            config_version=self._loop.session.config_version,
            audio=audio_of(turn, self._loop.session.audio_consent),
        )
        kept = await asyncio.to_thread(queue.keep, failed)
        if kept:
            self._quality.turn(Outcome.KEPT_FOR_REVIEW)
        return kept

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


__all__ = ["Heard", "Scorer", "Turn", "audio_of", "judged"]
