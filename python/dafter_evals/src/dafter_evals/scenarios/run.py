from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from dafter_core.enums import Stage

from .cost import Ledger, Rates
from .grade import Outcome, grade
from .judge import CallJudge, Verdicts
from .task import Task
from .transcript import Tokens
from .trial import Rig, TrialRun, play

NEEDS_VOICE = "needs --job naming a TTS and its fallback to test a TTS fault"
OVER_CAP = "skipped: its estimated cost would pass --max-inr"


@dataclass
class Played:
    outcomes: dict[str, list[Outcome]] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)


def spent(rates: Rates, run: TrialRun, judged: Tokens) -> Decimal:
    tokens = run.agent_tokens
    total = rates.agent_inr(tokens.input, tokens.output, run.llm_switches > 0)
    total += rates.user_inr(run.user_tokens.input, run.user_tokens.output)
    total += rates.judge_inr(judged.input, judged.output)
    if run.spoken is not None:
        total += rates.voice_inr(run.spoken.characters)
    return total


def trial_record(run: TrialRun, outcome: Outcome, cost: Decimal) -> dict[str, Any]:
    verdicts = outcome.verdicts
    return {
        "task": run.task.id,
        "language": run.task.language,
        "trial": run.trial,
        "set": run.task.scenario_set,
        "reviewed": run.task.reviewed,
        "tags": list(run.task.tags),
        "faults": sorted(str(f) for f in run.faults),
        "passed": outcome.passed,
        "checks": outcome.checks,
        "failed": outcome.failed_checks(),
        "error": run.error,
        "entities": outcome.entities,
        "toolCalls": outcome.tool_calls,
        "toolErrors": outcome.tool_errors,
        "llmSwitches": run.llm_switches,
        "spoken": run.spoken.to_dict() if run.spoken is not None else None,
        "endLanguage": run.state.language if run.state is not None else None,
        "effects": [
            {"tool": t, "arguments": a} for t, a in (run.state.effects if run.state else [])
        ],
        "judge": (
            {
                "verdicts": verdicts.verdicts,
                "reasoning": verdicts.reasoning,
                "error": verdicts.error,
            }
            if verdicts is not None
            else None
        ),
        "tokens": {
            "agent": [run.agent_tokens.input, run.agent_tokens.output],
            "user": [run.user_tokens.input, run.user_tokens.output],
        },
        "costInr": str(cost),
        "transcript": run.transcript.to_dict(),
    }


async def play_suite(
    tasks: Sequence[Task],
    rig: Rig,
    judge: CallJudge | None,
    rates: Rates,
    ledger: Ledger,
    trials: int,
    say: Callable[[str], None] = print,
) -> Played:
    played = Played()
    for task in tasks:
        played.tasks[task.id] = task
        faults = task.faults | rig.faults
        voiced = Stage.TTS in faults
        if voiced and rig.make_voice is None:
            played.skipped[task.id] = NEEDS_VOICE
            say(f"{task.id}: {NEEDS_VOICE}")
            continue
        if not ledger.affords(rates.trial_estimate(task, voiced, Stage.LLM in faults) * trials):
            played.skipped[task.id] = OVER_CAP
            say(f"{task.id}: {OVER_CAP}")
            continue
        outcomes: list[Outcome] = []
        for trial in range(1, trials + 1):
            run = await play(rig, task, trial)
            judged = Tokens()
            verdicts: Verdicts | None = None
            if judge is not None and task.expect.judge and run.error is None:
                verdicts = await judge.grade(task.expect.judge, run.transcript.shown(), judged)
            outcome = grade(run, verdicts)
            cost = spent(rates, run, judged)
            ledger.add(cost)
            outcomes.append(outcome)
            played.records.append(trial_record(run, outcome, cost))
            failed = ", ".join(outcome.failed_checks()) or "ok"
            say(f"{task.id} trial {trial}: {'pass' if outcome.passed else 'fail'} ({failed})")
        played.outcomes[task.id] = outcomes
    return played
