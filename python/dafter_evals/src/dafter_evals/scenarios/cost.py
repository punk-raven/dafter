from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from ..screen.catalog import Candidate, Price
from .task import Task
from .voice import VoiceChain

AGENT_INPUT_PER_TURN = 3000
AGENT_OUTPUT_PER_TURN = 200
USER_INPUT_PER_TURN = 1200
USER_OUTPUT_PER_TURN = 80
JUDGE_INPUT_PER_CALL = 4000
JUDGE_OUTPUT_PER_CALL = 300
SPOKEN_CHARACTERS_PER_TURN = 160


def _price(candidate: Candidate) -> Price:
    if candidate.price is None:
        raise ValueError(f"{candidate.id} is unpriced; add a price to the catalog first")
    return candidate.price


@dataclass(frozen=True, slots=True)
class Rates:
    agent: tuple[Price, ...]
    user: Price
    judge: Price | None
    voice: VoiceChain | None = None

    @classmethod
    def of(
        cls,
        agent: Sequence[Candidate],
        user: Candidate,
        judge: Candidate | None,
        voice: VoiceChain | None = None,
    ) -> Rates:
        return cls(
            agent=tuple(_price(c) for c in agent),
            user=_price(user),
            judge=_price(judge) if judge is not None else None,
            voice=voice,
        )

    def agent_inr(self, input_tokens: int, output_tokens: int, failed_over: bool) -> Decimal:
        prices = self.agent if failed_over else self.agent[:1]
        return max(p.cost_inr(input_tokens, output_tokens) for p in prices)

    def user_inr(self, input_tokens: int, output_tokens: int) -> Decimal:
        return self.user.cost_inr(input_tokens, output_tokens)

    def judge_inr(self, input_tokens: int, output_tokens: int) -> Decimal:
        return self.judge.cost_inr(input_tokens, output_tokens) if self.judge else Decimal(0)

    def voice_inr(self, characters: int) -> Decimal:
        if self.voice is None or characters == 0:
            return Decimal(0)
        cost = self.voice.cost_inr(characters)
        if cost is None:
            raise ValueError("a TTS in the job is unpriced; add it to the price table first")
        return cost

    def trial_estimate(self, task: Task, voiced: bool, failed_over: bool) -> Decimal:
        turns = task.max_turns if not task.user.turns else len(task.user.turns)
        spent = self.agent_inr(
            AGENT_INPUT_PER_TURN * turns, AGENT_OUTPUT_PER_TURN * turns, failed_over
        )
        if not task.user.turns:
            spent += self.user_inr(USER_INPUT_PER_TURN * turns, USER_OUTPUT_PER_TURN * turns)
        if task.expect.judge:
            spent += self.judge_inr(JUDGE_INPUT_PER_CALL, JUDGE_OUTPUT_PER_CALL)
        if voiced:
            spent += self.voice_inr(SPOKEN_CHARACTERS_PER_TURN * turns)
        return spent


class Ledger:
    def __init__(self, cap: Decimal) -> None:
        self.cap = cap
        self.spent = Decimal(0)

    def affords(self, estimate: Decimal) -> bool:
        return self.spent + estimate <= self.cap

    def add(self, inr: Decimal) -> None:
        self.spent += inr
