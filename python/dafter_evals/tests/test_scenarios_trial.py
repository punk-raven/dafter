from __future__ import annotations

import asyncio
import json
from typing import Any

from dafter_core.enums import Stage
from dafter_evals.scenarios import load
from dafter_evals.scenarios.grade import grade
from dafter_evals.scenarios.simulator import STOP, Said, Simulator, context
from dafter_evals.scenarios.task import Task
from dafter_evals.scenarios.transcript import AGENT_ROLE, CALLER_ROLE, Line, Tokens
from dafter_evals.scenarios.trial import Rig, TrialRun, play
from dafter_evals.screen.turn import Classify
from dafter_providers import openai_compat
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions, llm
from livekit.agents.types import NOT_GIVEN, NotGivenOr

classify: Classify = openai_compat.classifier(openai_compat.ENDPOINTS["google"])
LOOKUP = llm.FunctionToolCall(
    name="lookup_order", arguments=json.dumps({"order_id": "ORD-4821"}), call_id="lookup"
)
SHIPPED = "Your order has shipped and arrives on 12 October."


class ScriptStream(llm.LLMStream):
    def __init__(
        self,
        owner: Scripted,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(owner, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._owner = owner

    async def _run(self) -> None:
        step = self._owner.next_step()
        if isinstance(step, llm.FunctionToolCall):
            delta = llm.ChoiceDelta(role="assistant", tool_calls=[step])
        else:
            delta = llm.ChoiceDelta(role="assistant", content=step)
        self._event_ch.send_nowait(llm.ChatChunk(id="script", delta=delta))
        usage = llm.CompletionUsage(completion_tokens=10, prompt_tokens=100, total_tokens=110)
        self._event_ch.send_nowait(llm.ChatChunk(id="script", usage=usage))


class Scripted(llm.LLM[Any]):
    def __init__(self, steps: list[llm.FunctionToolCall | str]) -> None:
        super().__init__()
        self._steps = steps
        self.calls = 0

    @property
    def model(self) -> str:
        return "scripted"

    def next_step(self) -> llm.FunctionToolCall | str:
        self.calls += 1
        if self.calls <= len(self._steps):
            return self._steps[self.calls - 1]
        return f"Is there anything else, reply {self.calls}?"

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> llm.LLMStream:
        return ScriptStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


def task_named(language: str, suffix: str) -> Task:
    return next(t for t in load(language) if t.id == f"{language}-{suffix}")


def rig(agents: list[Scripted], faults: frozenset[Stage] = frozenset()) -> Rig:
    return Rig(
        make_llms=lambda: [(a, classify) for a in agents],
        user=(Scripted([STOP]), classify),
        policy="Follow the support policy.",
        timeout=5.0,
        turn_timeout=10.0,
        faults=faults,
    )


def played(task: Task, agents: list[Scripted], faults: frozenset[Stage] = frozenset()) -> TrialRun:
    return asyncio.run(play(rig(agents, faults), task, 1))


def test_a_simulated_order_status_trial_looks_up_the_order_and_passes() -> None:
    task = task_named("en-IN", "order-status")
    run = played(task, [Scripted([LOOKUP, SHIPPED])])
    assert run.error is None
    assert [c.tool for c in run.transcript.calls] == ["lookup_order"]
    assert run.transcript.lines[0].role == CALLER_ROLE
    assert SHIPPED in run.transcript.agent_text()
    outcome = grade(run, None)
    assert outcome.passed, outcome.failed_checks()
    assert outcome.entities == [{"kind": "date", "text": "12 October", "heard": True}]
    assert run.agent_tokens.input > 0


def test_a_reply_that_skips_the_lookup_fails_the_calls_check() -> None:
    run = played(task_named("en-IN", "order-status"), [Scripted([SHIPPED])])
    assert grade(run, None).failed_checks() == ["calls"]


def test_an_injected_llm_fault_completes_through_the_fallback() -> None:
    primary, backup = Scripted([LOOKUP, SHIPPED]), Scripted([LOOKUP, SHIPPED])
    run = played(task_named("en-IN", "fault-llm"), [primary, backup])
    assert primary.calls == 0
    assert run.llm_switches >= 1
    outcome = grade(run, None)
    assert outcome.checks["failover"]
    assert outcome.passed, outcome.failed_checks()


def test_a_fault_from_the_environment_without_a_fallback_fails_the_trial() -> None:
    run = played(
        task_named("en-IN", "order-status"), [Scripted([LOOKUP, SHIPPED])], frozenset({Stage.LLM})
    )
    outcome = grade(run, None)
    assert not outcome.passed
    assert "failover" in outcome.failed_checks()


def test_the_simulator_sees_the_agent_as_the_other_party() -> None:
    task = task_named("hi", "order-status")
    lines = [Line(CALLER_ROLE, "caller", task.user.opening), Line(AGENT_ROLE, "agent", "")]
    ctx = context(task, lines)
    roles = [m.role for m in ctx.messages()]
    assert roles == ["system", "assistant", "user"]
    assert "Hindi" in (ctx.messages()[0].text_content or "")


def test_the_simulator_stops_on_the_stop_token() -> None:
    task = task_named("hi", "order-status")
    tokens = Tokens()
    said = asyncio.run(
        Simulator(Scripted([f"धन्यवाद {STOP}"]), classify, task, tokens, 5.0).next([])
    )
    assert said == Said(None)
    assert tokens.input == 100
