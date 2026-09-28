from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import Any

from dafter_core.enums import ErrorCode

from ..turns import percentile
from .catalog import Candidate

CRITERIA = ("correctness", "language", "register", "speakability")
SAMPLE_SHARE = 0.2
HUMAN = {c: None for c in (*CRITERIA, "notes")}


@dataclass(frozen=True, slots=True)
class Record:
    date: str
    candidate: str
    role: str
    provider: str
    model: str
    language: str
    question_id: str
    question: str
    run: int
    reply: str | None
    ttft_ms: int | None
    ttfs_ms: int | None
    total_ms: int | None
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost: float | None
    currency: str | None
    cost_inr: float | None
    free_tier: bool | None
    tool_calls: int
    markdown: bool | None
    digits: bool | None
    error: str | None = None
    native_code: str | None = None
    verdicts: dict[str, str] = field(default_factory=dict)
    score: float | None = None
    judge_reasoning: str = ""
    judge_error: str | None = None
    tool_use: str | None = None
    expected_tools: list[str] = field(default_factory=list)
    tool_responses: list[list[str]] = field(default_factory=list)
    missed_tools: list[str] = field(default_factory=list)
    unexpected_tools: list[str] = field(default_factory=list)
    parallel_tool_calls: bool = False
    tool_quoted: bool | None = None
    claimed_without_call: bool | None = None

    @property
    def answered(self) -> bool:
        return self.error is None and self.reply is not None

    @property
    def tools_clean(self) -> bool:
        return not (
            self.missed_tools
            or self.unexpected_tools
            or self.parallel_tool_calls
            or self.tool_quoted is False
            or self.claimed_without_call
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Row:
    candidate: Candidate
    records: list[Record]
    skipped: str | None = None

    def ms(self, key: str, p: float) -> int | None:
        values = [getattr(r, key) for r in self.records if r.answered]
        return percentile([v for v in values if v is not None], p)

    def _count(self, code: ErrorCode) -> int:
        return sum(1 for r in self.records if r.error == str(code))

    def _pass_rate(self, criterion: str) -> float | None:
        judged = [r.verdicts[criterion] for r in self.records if criterion in r.verdicts]
        return sum(1 for v in judged if v == "pass") / len(judged) if judged else None

    def quality(self) -> float | None:
        scores = [r.score for r in self.records if r.score is not None]
        return sum(scores) / len(scores) if scores else None

    def cost_per_1k(self) -> Decimal | None:
        costs = [
            Decimal(str(r.cost_inr)) for r in self.records if r.answered and r.cost_inr is not None
        ]
        return sum(costs, Decimal(0)) * 1000 / len(costs) if costs else None

    def tool_summary(self) -> dict[str, int]:
        probes = [r for r in self.records if r.answered and r.expected_tools]
        return {
            "toolProbes": len(probes),
            "toolProbesClean": sum(1 for r in probes if r.tools_clean),
            "toolMissed": sum(1 for r in self.records if r.missed_tools),
            "toolUnexpected": sum(1 for r in self.records if r.unexpected_tools),
            "parallelToolCalls": sum(1 for r in self.records if r.parallel_tool_calls),
            "toolQuotedWrong": sum(1 for r in self.records if r.tool_quoted is False),
            "claimedWithoutCall": sum(1 for r in self.records if r.claimed_without_call),
        }

    def summary(self) -> dict[str, Any]:
        per_1k = self.cost_per_1k()
        price = self.candidate.price
        return {
            "candidate": self.candidate.id,
            "role": self.candidate.role,
            "provider": self.candidate.ref.provider,
            "model": self.candidate.ref.model,
            "skipped": self.skipped,
            "calls": len(self.records),
            "answered": sum(1 for r in self.records if r.answered),
            "errors": sum(1 for r in self.records if r.error),
            "rateLimited": self._count(ErrorCode.RATE_LIMITED),
            "quotaExceeded": self._count(ErrorCode.QUOTA_EXCEEDED),
            "ttftP50Ms": self.ms("ttft_ms", 0.5),
            "ttftP95Ms": self.ms("ttft_ms", 0.95),
            "ttfsP50Ms": self.ms("ttfs_ms", 0.5),
            "ttfsP95Ms": self.ms("ttfs_ms", 0.95),
            "quality": self.quality(),
            "passRates": {c: self._pass_rate(c) for c in CRITERIA},
            "markdownReplies": sum(1 for r in self.records if r.markdown),
            "digitReplies": sum(1 for r in self.records if r.digits),
            "reasoningTokens": sum(r.reasoning_tokens for r in self.records),
            "reasoningReplies": sum(1 for r in self.records if r.reasoning_tokens),
            "toolUse": [r.tool_use for r in self.records if r.tool_use is not None],
            **self.tool_summary(),
            "costPer1kRepliesInr": float(per_1k) if per_1k is not None else None,
            "priceCurrency": price.currency if price else None,
            "freeTier": self.candidate.free_tier,
            "unverified": list(self.candidate.unverified),
        }


def merged(per_language: list[list[Row]]) -> list[Row]:
    order: dict[str, Row] = {}
    for rows in per_language:
        for row in rows:
            seen = order.get(row.candidate.id)
            if seen is None:
                order[row.candidate.id] = Row(row.candidate, list(row.records), row.skipped)
            else:
                seen.records.extend(row.records)
    return list(order.values())


def rank(rows: list[Row]) -> list[Row]:
    def key(row: Row) -> tuple[int, float, float]:
        quality = row.quality()
        ttfs = row.ms("ttfs_ms", 0.5)
        return (
            1 if row.skipped or quality is None else 0,
            -(quality or 0.0),
            float(ttfs) if ttfs is not None else math.inf,
        )

    return sorted(rows, key=key)


def _num(value: float | int | None, fmt: str = "{}") -> str:
    return "-" if value is None else fmt.format(value)


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{round(value * 100)}%"


def _probes(s: dict[str, Any]) -> str:
    return f"{s['toolProbesClean']}/{s['toolProbes']}" if s["toolProbes"] else "-"


def table(rows: list[Row], date: str, judge: str | None, languages: Sequence[str]) -> str:
    head = [
        f"Stage 4 LLM screen, {date}, languages: {', '.join(languages)}, judge: {judge or 'none'}",
        "",
        "| # | candidate | provider/model | answered | rate limited | TTFT p50/p95 ms "
        "| TTFS p50/p95 ms | quality | correct | language | register | speakable "
        "| tool probes clean | INR per 1k replies | free tier |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines = []
    place = 0
    for row in rank(rows):
        s = row.summary()
        if row.skipped:
            lines.append(
                f"| - | {s['candidate']} | {s['provider']}/{s['model']} | skipped: "
                f"{row.skipped} | | | | | | | | | | | |"
            )
            continue
        rates = s["passRates"]
        ranked = s["quality"] is not None
        if ranked:
            place += 1
        per_1k = s["costPer1kRepliesInr"]
        cost = "-" if per_1k is None else f"{per_1k:.4f}"
        lines.append(
            " | ".join(
                [
                    f"| {place if ranked else '-'}",
                    s["candidate"],
                    f"{s['provider']}/{s['model']}",
                    f"{s['answered']}/{s['calls']}",
                    str(s["rateLimited"]),
                    f"{_num(s['ttftP50Ms'])}/{_num(s['ttftP95Ms'])}",
                    f"{_num(s['ttfsP50Ms'])}/{_num(s['ttfsP95Ms'])}",
                    _num(s["quality"], "{:.2f}"),
                    _pct(rates["correctness"]),
                    _pct(rates["language"]),
                    _pct(rates["register"]),
                    _pct(rates["speakability"]),
                    _probes(s),
                    cost,
                    {True: "yes", False: "no", None: "not stated"}[s["freeTier"]] + " |",
                ]
            )
        )
    return "\n".join([*head, *lines]) + "\n"


def sample(
    records: list[Record], seed: int, judged: bool = False, share: float = SAMPLE_SHARE
) -> list[dict[str, Any]]:
    pool = [r for r in records if r.answered and (r.verdicts or not judged)]
    if not pool:
        return []
    k = max(1, math.ceil(len(pool) * share))
    picked = random.Random(seed).sample(pool, k)
    return [
        {
            "date": r.date,
            "candidate": r.candidate,
            "model": r.model,
            "language": r.language,
            "questionId": r.question_id,
            "question": r.question,
            "run": r.run,
            "reply": r.reply,
            "judge": {"verdicts": r.verdicts, "reasoning": r.judge_reasoning},
            "human": dict(HUMAN),
        }
        for r in picked
    ]
