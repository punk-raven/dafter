from __future__ import annotations

import math
import random
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
    cost: float | None
    currency: str | None
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

    @property
    def answered(self) -> bool:
        return self.error is None and self.reply is not None

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
        costs = [Decimal(str(r.cost)) for r in self.records if r.answered and r.cost is not None]
        return sum(costs, Decimal(0)) * 1000 / len(costs) if costs else None

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
            "toolUse": [r.tool_use for r in self.records if r.tool_use is not None],
            "costPer1kReplies": float(per_1k) if per_1k is not None else None,
            "currency": price.currency if price else None,
            "freeTier": self.candidate.free_tier,
            "unverified": list(self.candidate.unverified),
        }


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


def table(rows: list[Row], date: str, judge: str | None) -> str:
    head = [
        f"Stage 4 LLM screen, {date}, judge: {judge or 'none'}",
        "",
        "| # | candidate | provider/model | answered | rate limited | TTFT p50/p95 ms "
        "| TTFS p50/p95 ms | quality | correct | language | register | speakable "
        "| cost per 1k replies | free tier |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines = []
    for n, row in enumerate(rank(rows), start=1):
        s = row.summary()
        if row.skipped:
            lines.append(
                f"| - | {s['candidate']} | {s['provider']}/{s['model']} | skipped: "
                f"{row.skipped} | | | | | | | | | | |"
            )
            continue
        rates = s["passRates"]
        cost = (
            f"{s['currency']} {s['costPer1kReplies']:.4f}"
            if s["costPer1kReplies"] is not None
            else "-"
        )
        lines.append(
            " | ".join(
                [
                    f"| {n}",
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
                    cost,
                    {True: "yes", False: "no", None: "not stated"}[s["freeTier"]] + " |",
                ]
            )
        )
    return "\n".join([*head, *lines]) + "\n"


def sample(records: list[Record], seed: int, share: float = SAMPLE_SHARE) -> list[dict[str, Any]]:
    answered = [r for r in records if r.answered]
    if not answered:
        return []
    k = max(1, math.ceil(len(answered) * share))
    picked = random.Random(seed).sample(answered, k)
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
