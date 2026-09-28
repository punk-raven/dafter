from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from dafter_core.config import ProviderRef
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError
from dafter_providers import vendor_for
from livekit.agents import llm

from .bank import Bank, Question
from .catalog import Candidate
from .judge import Judge, digits, markdown
from .report import Record, Row
from .tools import check
from .turn import Classify, Now, Reply, ask

Build = Callable[[ProviderRef], tuple[llm.LLM[Any], Classify]]


@dataclass(frozen=True, slots=True)
class Settings:
    date: str
    runs: int = 5
    pause: float = 0.0
    timeout: float = 20.0


def build(ref: ProviderRef) -> tuple[llm.LLM[Any], Classify]:
    vendor = vendor_for(ref, Stage.LLM)
    if vendor.llm is None:
        raise DafterError(ErrorCode.INTERNAL, "a registered llm vendor lost its factory")
    return vendor.llm(ref), vendor.classify


def _cost(candidate: Candidate, reply: Reply, inr: bool = False) -> float | None:
    price = candidate.price
    if price is None or reply.error is not None:
        return None
    spend = price.cost_inr if inr else price.cost
    return float(spend(reply.input_tokens, reply.output_tokens))


def record(
    settings: Settings, bank: Bank, candidate: Candidate, question: Question, run: int, reply: Reply
) -> Record:
    err = reply.error
    tools = check(question, reply.responses, reply.text)
    return Record(
        date=settings.date,
        candidate=candidate.id,
        role=candidate.role,
        provider=candidate.ref.provider,
        model=candidate.ref.model or "",
        language=bank.language,
        question_id=question.id,
        question=question.text,
        run=run,
        reply=reply.text,
        ttft_ms=reply.ttft_ms,
        ttfs_ms=reply.ttfs_ms,
        total_ms=reply.total_ms,
        input_tokens=reply.input_tokens,
        output_tokens=reply.output_tokens,
        reasoning_tokens=reply.reasoning_tokens,
        cost=_cost(candidate, reply),
        currency=candidate.price.currency if candidate.price else None,
        cost_inr=_cost(candidate, reply, inr=True),
        free_tier=candidate.free_tier,
        tool_calls=reply.tool_calls,
        markdown=markdown(reply.text) if reply.text else None,
        digits=digits(reply.text) if reply.text else None,
        error=str(err.code) if err else None,
        native_code=err.provider.native_code if err and err.provider else None,
        expected_tools=list(question.tools),
        tool_responses=[list(r) for r in reply.responses],
        missed_tools=tools.missed if err is None else [],
        unexpected_tools=tools.unexpected,
        parallel_tool_calls=tools.parallel,
        tool_quoted=tools.quoted,
        claimed_without_call=tools.claimed_without_call if err is None else None,
    )


async def judged(judge: Judge, rec: Record, reply: Reply, pause: float) -> Record:
    if rec.reply is None:
        return rec
    scores = await judge.score(rec.question, rec.reply)
    await asyncio.sleep(pause)
    tool_use = None
    if (reply.tool_calls or rec.expected_tools) and reply.history is not None:
        tool_use = await judge.tool_use(reply.history)
        await asyncio.sleep(pause)
    return replace(
        rec,
        verdicts=scores.verdicts,
        score=scores.score(),
        judge_reasoning=scores.reasoning,
        judge_error=scores.error,
        tool_use=tool_use,
    )


class Screen:
    def __init__(
        self,
        settings: Settings,
        bank: Bank,
        instructions: str,
        judge: Judge | None,
        build: Build = build,
        now: Now = time.perf_counter,
        say: Callable[[str], None] = print,
        tools: list[llm.Tool] | None = None,
    ) -> None:
        self.settings = settings
        self.bank = bank
        self.instructions = instructions
        self.judge = judge
        self._build = build
        self._now = now
        self._say = say
        self._tools = tools or []

    async def run(self, candidates: tuple[Candidate, ...]) -> list[Row]:
        built: dict[str, tuple[llm.LLM[Any], Classify]] = {}
        skipped: dict[str, str] = {}
        for c in candidates:
            try:
                built[c.id] = self._build(c.ref)
            except DafterError as exc:
                skipped[c.id] = f"{exc.code}: {exc.message}"
                self._say(f"{c.id}: skipped, {exc.message}")
        records: dict[str, list[Record]] = {c.id: [] for c in candidates}
        try:
            for run in range(1, self.settings.runs + 1):
                for c in candidates:
                    if c.id in built:
                        records[c.id].extend(await self._pass(c, *built[c.id], run))
        finally:
            for model, _ in built.values():
                await model.aclose()
        return [Row(c, records[c.id], skipped.get(c.id)) for c in candidates]

    async def _pass(
        self, candidate: Candidate, model: llm.LLM[Any], classify: Classify, run: int
    ) -> list[Record]:
        out: list[Record] = []
        for q in self.bank.questions:
            reply = await ask(
                model,
                classify,
                self.instructions,
                q.text,
                self.settings.timeout,
                self._now,
                self._tools,
            )
            await asyncio.sleep(self.settings.pause)
            rec = record(self.settings, self.bank, candidate, q, run, reply)
            if self.judge is not None:
                rec = await judged(self.judge, rec, reply, self.settings.pause)
            self._say(f"{candidate.id} run {run} {q.id}: {rec.error or 'ok'}")
            out.append(rec)
        return out
