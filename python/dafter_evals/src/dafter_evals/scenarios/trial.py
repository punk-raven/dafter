from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from dafter_core.enums import Stage
from dafter_core.errors import DafterError
from dafter_providers import fallback
from dafter_runtime.answering import turn_text
from dafter_runtime.personas import persona_for
from dafter_runtime.toolbox import Answering
from livekit.agents import AgentSession, APIConnectOptions, RunResult, llm
from livekit.agents.metrics import LLMModelUsage
from livekit.agents.voice.agent_session import SessionConnectOptions

from ..screen.turn import Classify
from .simulator import Simulator
from .task import CALLER, SCRIPTED, TO_THE_ROOM, Task
from .transcript import AGENT_ROLE, CALLER_ROLE, Call, Line, Tokens, Transcript
from .voice import Spoken, Voice
from .world import State, WorldTools, build_world

TOOL_ROLE = "tool"
END_CALL = "end_call"

Built = tuple[llm.LLM[Any], Classify]
MakeLLMs = Callable[[], list[Built]]
MakeVoice = Callable[[Task, bool], Voice]


@dataclass
class TrialRun:
    task: Task
    trial: int
    transcript: Transcript = field(default_factory=Transcript)
    state: State | None = None
    replied: list[bool] = field(default_factory=list)
    error: str | None = None
    agent_tokens: Tokens = field(default_factory=Tokens)
    user_tokens: Tokens = field(default_factory=Tokens)
    llm_switches: int = 0
    spoken: Spoken | None = None
    faults: frozenset[Stage] = frozenset()

    @property
    def ended(self) -> bool:
        return any(c.tool == END_CALL and not c.error for c in self.transcript.calls)


@dataclass(frozen=True, slots=True)
class Rig:
    make_llms: MakeLLMs
    user: Built
    policy: str
    timeout: float
    turn_timeout: float
    make_voice: MakeVoice | None = None
    faults: frozenset[Stage] = frozenset()


def _arguments(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"_unparsed": raw}
    return parsed if isinstance(parsed, dict) else {"_unparsed": raw}


def _agent_tokens(session: AgentSession[Any]) -> tuple[int, int]:
    used = [u for u in session.usage.model_usage if isinstance(u, LLMModelUsage)]
    return sum(u.input_tokens for u in used), sum(u.output_tokens for u in used)


def record(run: TrialRun, result: RunResult[None]) -> list[str]:
    said: list[str] = []
    pending: dict[str, tuple[str, dict[str, Any]]] = {}
    for event in result.events:
        if event.type == "message" and event.item.role == "assistant":
            text = event.item.text_content or ""
            if text.strip():
                said.append(text)
                run.transcript.lines.append(Line(AGENT_ROLE, "agent", text))
        elif event.type == "function_call":
            pending[event.item.call_id] = (event.item.name, _arguments(event.item.arguments))
        elif event.type == "function_call_output":
            name, arguments = pending.pop(event.item.call_id, (event.item.name, {}))
            output = event.item.output
            run.transcript.calls.append(Call(name, arguments, output, event.item.is_error))
            shown = f"{name}({json.dumps(arguments, ensure_ascii=False)}) returned {output}"
            run.transcript.lines.append(Line(TOOL_ROLE, TOOL_ROLE, shown))
    for name, arguments in pending.values():
        run.transcript.calls.append(Call(name, arguments, None, True))
    return said


def _agent_llm(rig: Rig, faults: frozenset[Stage], run: TrialRun) -> tuple[llm.LLM[Any], list[Any]]:
    made = rig.make_llms()
    if not made:
        raise ValueError("no agent model was built")
    primary = made[0][0]
    chained = fallback.llm(
        primary,
        [m for m, _ in made[1:]],
        Stage.LLM in faults,
        [fallback.classified_first(c) for _, c in made],
    )

    def switched() -> None:
        run.llm_switches += 1

    if chained is not primary:
        fallback.follow_switches(chained, Stage.LLM, switched)
    return chained, [m for m, _ in made]


async def _turn(
    session: AgentSession[Any], run: TrialRun, text: str, timeout: float, voice: Voice | None
) -> list[str]:
    async with asyncio.timeout(timeout):
        result: RunResult[None] = await session.run(user_input=text)
    said = record(run, result)
    if voice is not None:
        for reply in said:
            await voice.say(reply)
    return said


async def play(rig: Rig, task: Task, trial: int) -> TrialRun:
    faults = task.faults | rig.faults
    run = TrialRun(task=task, trial=trial, faults=faults)
    speaking = [task.world.people[0].id if task.world.people else CALLER]
    built = build_world(task, caller=lambda: speaking[0])
    run.state = built.state
    agent_llm, made = _agent_llm(rig, faults, run)
    voice = None
    if rig.make_voice is not None and Stage.TTS in faults:
        voice = rig.make_voice(task, True)
    options = SessionConnectOptions(
        llm_conn_options=APIConnectOptions(max_retry=0, timeout=rig.timeout)
    )
    session: AgentSession[None] = AgentSession(llm=agent_llm, conn_options=options)
    persona = persona_for(task.persona, task.language, task.agent_name)
    agent = Answering(
        persona.instructions,
        built.registry,
        caller=lambda: speaking[0],
        switching=built.switching,
        name=task.agent_name,
    )
    await agent.update_tools(built.tools())
    try:
        async with session:
            await session.start(agent)
            agent.brief(rig.policy)
            await agent.update_instructions(f"{persona.instructions}\n\n{rig.policy}")
            if task.user.mode == SCRIPTED:
                await _scripted(session, run, task, built, speaking, rig, voice)
            else:
                await _simulated(session, run, task, built, rig, voice)
    except DafterError as exc:
        run.error = f"{exc.code}: {exc.message}"
    except TimeoutError:
        run.error = "a turn timed out"
    except Exception as exc:
        run.error = type(exc).__name__
    finally:
        run.agent_tokens.add(*_agent_tokens(session))
        for model in made:
            await model.aclose()
        if voice is not None:
            run.spoken = voice.spoken
            await voice.aclose()
    return run


async def _scripted(
    session: AgentSession[Any],
    run: TrialRun,
    task: Task,
    built: WorldTools,
    speaking: list[str],
    rig: Rig,
    voice: Voice | None,
) -> None:
    for turn in task.user.turns:
        speaking[0] = turn.speaker
        label = built.roster.label(turn.speaker)
        run.transcript.lines.append(Line(CALLER_ROLE, label, turn.text))
        built.registry.heard(turn.speaker, turn.text)
        said = turn_text(built.roster, turn.speaker, turn.text, [], turn.to == TO_THE_ROOM)
        if not task.group:
            said = turn.text
        replies = await _turn(session, run, said, rig.turn_timeout, voice)
        run.replied.append(bool(replies))
        if run.ended:
            return


async def _simulated(
    session: AgentSession[Any],
    run: TrialRun,
    task: Task,
    built: WorldTools,
    rig: Rig,
    voice: Voice | None,
) -> None:
    model, classify = rig.user
    simulator = Simulator(model, classify, task, run.user_tokens, rig.timeout)
    text: str | None = task.user.opening
    for _ in range(task.max_turns):
        if text is None:
            return
        run.transcript.lines.append(Line(CALLER_ROLE, "caller", text))
        built.registry.heard(CALLER, text)
        await _turn(session, run, text, rig.turn_timeout, voice)
        if run.ended:
            return
        heard = [line for line in run.transcript.lines if line.role != TOOL_ROLE]
        said = await simulator.next(heard)
        if said.error is not None:
            run.error = f"user simulator: {said.error}"
            return
        text = said.text
