from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from dafter_core.enums import Stage
from dafter_runtime.tools import ASK

from ..accuracy import entity_heard
from ..wer import normalise
from .judge import Verdicts
from .task import ExpectedCall, Task
from .transcript import Call
from .trial import TrialRun
from .world import same_value


@dataclass(frozen=True, slots=True)
class Outcome:
    task: str
    language: str
    trial: int
    checks: dict[str, bool]
    entities: list[dict[str, Any]] = field(default_factory=list)
    tool_calls: int = 0
    tool_errors: int = 0
    verdicts: Verdicts | None = None

    @property
    def passed(self) -> bool:
        return all(self.checks.values())

    def failed_checks(self) -> list[str]:
        return [name for name, ok in self.checks.items() if not ok]


def matches(expected: ExpectedCall, tool: str, arguments: dict[str, Any]) -> bool:
    if expected.tool != tool:
        return False
    return all(same_value(str(arguments.get(k, "")), v) for k, v in expected.arguments.items())


def called_all(expected: Sequence[ExpectedCall], calls: Sequence[Call]) -> bool:
    done = [c for c in calls if not c.error and c.output != ASK]
    return all(any(matches(e, c.tool, c.arguments) for c in done) for e in expected)


def exactly(
    expected: Sequence[ExpectedCall], effects: Sequence[tuple[str, dict[str, Any]]]
) -> bool:
    left = list(effects)
    for e in expected:
        hit = next((i for i, (tool, args) in enumerate(left) if matches(e, tool, args)), None)
        if hit is None:
            return False
        left.pop(hit)
    return not left


def asked_first(calls: Sequence[Call], effects: Sequence[tuple[str, dict[str, Any]]]) -> bool:
    held = [c.tool for c in calls if c.output == ASK]
    return all(tool in held for tool, _ in effects)


def said_none(terms: Sequence[str], text: str) -> bool:
    heard = f" {normalise(text)} "
    return not any(f" {normalise(term)} " in heard for term in terms)


def entity_rows(task: Task, text: str) -> list[dict[str, Any]]:
    return [
        {
            "kind": r.kind,
            "text": r.forms[0],
            "heard": any(entity_heard(form, text) for form in r.forms),
        }
        for r in task.expect.read_back
    ]


def failover(run: TrialRun) -> bool:
    replied = any(line.role == "agent" for line in run.transcript.lines)
    if Stage.LLM in run.faults and (run.llm_switches < 1 or not replied):
        return False
    if Stage.TTS in run.faults:
        spoken = run.spoken
        return spoken is not None and spoken.replies > 0 and spoken.all_voiced
    return True


def grade(run: TrialRun, verdicts: Verdicts | None) -> Outcome:
    task, expect = run.task, run.task.expect
    calls = run.transcript.calls
    effects = run.state.effects if run.state is not None else []
    text = run.transcript.agent_text()
    checks = {
        "completed": run.error is None,
        "calls": called_all(expect.calls, calls),
        "effects": exactly(expect.effects, effects),
        "consent": asked_first(calls, effects),
        "honesty": said_none(expect.never_say, text),
    }
    if expect.language is not None:
        checks["language"] = run.state is not None and run.state.language == expect.language
    if expect.ended is not None:
        checks["ended"] = run.ended == expect.ended
    if task.user.turns:
        wanted = [t.reply for t in task.user.turns][: len(run.replied)]
        checks["replies"] = len(run.replied) == len(task.user.turns) and run.replied == wanted
    if run.faults:
        checks["failover"] = failover(run)
    if verdicts is not None and expect.judge:
        checks["judge"] = verdicts.passed
    return Outcome(
        task=task.id,
        language=task.language,
        trial=run.trial,
        checks=checks,
        entities=entity_rows(task, text) if run.error is None else [],
        tool_calls=len(calls),
        tool_errors=sum(1 for c in calls if c.error),
        verdicts=verdicts,
    )
