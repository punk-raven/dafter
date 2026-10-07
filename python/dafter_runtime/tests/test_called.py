from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dafter_core.events import parse_event
from dafter_core.hashing import seal
from dafter_runtime.addressing import Gate
from dafter_runtime.answering import Roster, Voice
from dafter_runtime.called import command, meaning_for
from dafter_runtime.events import SessionEvents
from dafter_runtime.naming import Matcher
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.toolbox import Answering, registry_for
from dafter_runtime.worker import room_options
from livekit.agents import AgentSession, llm
from stub_llm import REPLY, StubLLM, said

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
ASHA = "p_4b81e0d7"
RAVI = "p_9d02c3aa"
WINDOW_S = 20.0


def called_plan(stays_awake: bool = False) -> Plan:
    doc = json.loads(JOB.read_bytes())
    doc["agent"]["addressing"]["mode"] = "transcript"
    doc["agent"]["addressing"]["staysAwake"] = stays_awake
    sealed, _ = seal(json.dumps(doc))
    return plan(load(sealed), "dafter-py")


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class Pending:
    def __init__(self, delay: float, callback: Callable[[], None]) -> None:
        self.delay = delay
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class Scheduler:
    def __init__(self) -> None:
        self.pending: list[Pending] = []

    def __call__(self, delay: float, callback: Callable[[], None]) -> Pending:
        timer = Pending(delay, callback)
        self.pending.append(timer)
        return timer

    def live(self) -> list[Pending]:
        return [t for t in self.pending if not t.cancelled]

    def fire(self) -> None:
        [timer] = self.live()
        timer.cancelled = True
        timer.callback()


class Call:
    def __init__(self, p: Plan, session: AgentSession[None]) -> None:
        self.sent: list[bytes] = []
        self.events = SessionEvents(p.config, self._publish)
        session.on("agent_state_changed", lambda ev: self.events.changed(ev.new_state))
        self.roster = Roster()
        self.roster.join(ASHA)
        self.roster.join(RAVI)
        self.voice = Voice(session, self.roster, interruptible=True)
        self.clock = Clock()
        self.scheduler = Scheduler()
        self.gate = Gate(
            Matcher.for_agent(p.config.agent),
            WINDOW_S,
            self.voice,
            clock=self.clock,
            schedule=self.scheduler,
            name=p.config.agent.name or "",
            meaning=meaning_for(p),
            stays_awake=p.config.agent.addressing.stays_awake,
        )
        self.voice.announce = self.events.addressed
        session.on("agent_state_changed", lambda ev: self.gate.agent_state(ev.new_state))
        self.registry = registry_for(
            p, session, self.roster, lambda: self.gate.addressee, self.gate.go_quiet
        )
        self.voice.before_answer = self.registry.heard
        self.agent = Answering(p.persona.instructions, self.registry, lambda: self.gate.addressee)

    async def _publish(self, body: bytes) -> None:
        self.sent.append(body)

    async def states(self) -> list[dict[str, Any]]:
        await self.events.drain()
        return [parse_event(body).payload for body in self.sent]

    async def answered(self) -> None:
        assert self.voice.reply is not None
        await self.voice.reply
        await asyncio.sleep(0)


def run_call(
    script: Callable[[Call, StubLLM], Any],
    calls: list[str] | None = None,
    stays_awake: bool = False,
) -> StubLLM:
    p = called_plan(stays_awake)
    stub = StubLLM(calls=calls)

    async def run() -> None:
        async with AgentSession[None](llm=stub) as session:
            call = Call(p, session)
            await session.start(call.agent)
            await script(call, stub)

    asyncio.run(run())
    return stub


def test_the_called_plan_names_the_agent_and_prompts_the_recognizer() -> None:
    p = called_plan()
    assert p.called_by_name
    assert p.persona.instructions.startswith("You are Nivya, ")
    assert "calls you by your name" in p.persona.instructions
    assert p.stt_prompt == "Nivya, निव्या, ನಿವ್ಯ, ನಿವ್ಯಾ, నివ్య, నివ్యా"
    assert p.voice_turn_handling["turn_detection"] == "manual"
    assert p.turn_handling["turn_detection"] == "stt"


def test_the_voice_session_publishes_speech_and_hears_no_one_itself() -> None:
    options = room_options(called_plan(), 24000)
    assert options.get_audio_input_options() is None
    assert options.get_text_input_options() is None
    output = options.get_audio_output_options()
    assert output is not None and output.sample_rate == 24000
    assert options.close_on_disconnect is False


def test_the_agent_stays_silent_until_called_then_answers_with_what_others_said() -> None:
    async def script(call: Call, stub: StubLLM) -> None:
        call.gate.heard(RAVI, "कल की मीटिंग दस बजे है")
        call.gate.heard(ASHA, "मैंने कल निव्या को बताया था")
        call.gate.heard(ASHA, "नव्या, इधर आओ")
        assert call.gate.dormant
        assert stub.requests == []

        call.gate.heard(ASHA, "निव्या, मीटिंग कितने बजे है?")
        assert call.gate.addressee == ASHA
        await call.answered()

    stub = run_call(script)
    [request] = stub.requests
    system = said(request)[0][1] or ""
    assert system.startswith("You are Nivya, ")
    assert said(request)[1:] == [
        (
            "user",
            "[Speaker 2, not to you] कल की मीटिंग दस बजे है\n"
            "[Speaker 1, not to you] मैंने कल निव्या को बताया था\n"
            "[Speaker 1, not to you] नव्या, इधर आओ\n"
            "[Speaker 1, to you] निव्या, मीटिंग कितने बजे है?",
        )
    ]


def test_the_caller_follows_up_without_the_name() -> None:
    async def script(call: Call, stub: StubLLM) -> None:
        call.gate.heard(ASHA, "निव्या, मीटिंग कितने बजे है?")
        await call.answered()
        call.gate.heard(ASHA, "और कहाँ है?")
        await call.answered()

    stub = run_call(script)
    assert len(stub.requests) == 2
    assert said(stub.requests[1])[1:] == [
        ("user", "[Speaker 1, to you] निव्या, मीटिंग कितने बजे है?"),
        ("assistant", REPLY),
        ("user", "[Speaker 1, to you] और कहाँ है?"),
    ]


def test_someone_else_speaking_closes_the_follow_up_so_the_caller_names_her_again() -> None:
    async def script(call: Call, stub: StubLLM) -> None:
        call.gate.heard(ASHA, "निव्या, मीटिंग कितने बजे है?")
        await call.answered()
        call.gate.heard(RAVI, "मुझे भी जानना है")
        assert call.gate.dormant
        call.gate.heard(ASHA, "और कहाँ है?")
        assert len(stub.requests) == 1
        call.gate.heard(ASHA, "निव्या, और कहाँ है?")
        await call.answered()

    stub = run_call(script)
    assert len(stub.requests) == 2
    assert said(stub.requests[1])[-1] == (
        "user",
        "[Speaker 2, not to you] मुझे भी जानना है\n"
        "[Speaker 1, not to you] और कहाँ है?\n"
        "[Speaker 1, to you] निव्या, और कहाँ है?",
    )


def test_a_backchannel_from_someone_else_keeps_the_follow_up_open() -> None:
    async def script(call: Call, stub: StubLLM) -> None:
        call.gate.heard(ASHA, "निव्या, मीटिंग कितने बजे है?")
        await call.answered()
        call.gate.heard(RAVI, "हम्म")
        assert not call.gate.dormant
        call.gate.heard(ASHA, "और कहाँ है?")
        await call.answered()

    stub = run_call(script)
    assert len(stub.requests) == 2


def test_the_window_runs_from_the_end_of_the_reply_then_it_sleeps() -> None:
    async def script(call: Call, stub: StubLLM) -> None:
        call.gate.heard(ASHA, "Nivya, what time is it?")
        await call.answered()
        [window] = call.scheduler.live()
        assert window.delay == WINDOW_S
        assert not call.gate.dormant

        call.scheduler.fire()
        assert call.gate.dormant
        call.gate.heard(ASHA, "and tomorrow?")
        assert len(stub.requests) == 1

    run_call(script)


def test_another_person_calling_takes_over() -> None:
    async def script(call: Call, stub: StubLLM) -> None:
        call.gate.heard(ASHA, "Nivya, what time is it?")
        await call.answered()
        call.gate.heard(RAVI, "Hey Nivya, who is here?")
        await call.answered()
        assert call.gate.addressee == RAVI
        call.gate.heard(ASHA, "and tomorrow?")
        assert len(stub.requests) == 2

    run_call(script)


def test_stop_sends_it_to_sleep_at_once_and_a_bare_stop_only_when_awake() -> None:
    async def script(call: Call, stub: StubLLM) -> None:
        call.gate.heard(RAVI, "bas")
        assert call.gate.dormant
        call.gate.heard(ASHA, "Nivya, what time is it?")
        await call.answered()
        call.gate.heard(RAVI, "बस")
        assert call.gate.dormant
        assert call.scheduler.live() == []

        call.gate.heard(ASHA, "Nivya, what time is it?")
        await call.answered()
        call.gate.heard(ASHA, "निव्या, बस")
        assert call.gate.dormant
        call.gate.heard(ASHA, "and tomorrow?")
        assert len(stub.requests) == 2

    run_call(script)


def test_the_caller_leaving_sends_it_to_sleep() -> None:
    async def script(call: Call, stub: StubLLM) -> None:
        call.gate.heard(ASHA, "Nivya, what time is it?")
        await call.answered()
        call.gate.left(RAVI)
        assert call.gate.addressee == ASHA
        call.gate.left(ASHA)
        assert call.gate.dormant

    run_call(script)


def test_everything_said_before_she_was_called_is_context_however_long_ago() -> None:
    async def script(call: Call, stub: StubLLM) -> None:
        call.gate.heard(RAVI, "old news")
        call.clock.now += 3600
        call.gate.heard(RAVI, "fresh news")
        call.gate.heard(ASHA, "Nivya, what time is it?")
        await call.answered()

    stub = run_call(script)
    assert said(stub.requests[0])[-1] == (
        "user",
        "[Speaker 2, not to you] old news\n"
        "[Speaker 2, not to you] fresh news\n"
        "[Speaker 1, to you] Nivya, what time is it?",
    )


def test_roster_labels_by_name_or_by_order_of_joining() -> None:
    roster = Roster()
    roster.join(ASHA)
    roster.join(RAVI, "Ravi")
    roster.join(ASHA)
    roster.leave(ASHA)
    assert (roster.label(ASHA), roster.label(RAVI), roster.present()) == (
        "Speaker 1",
        "Ravi",
        ["Ravi"],
    )


def test_the_agent_can_send_itself_to_sleep_with_go_quiet() -> None:
    async def script(call: Call, stub: StubLLM) -> None:
        call.gate.heard(ASHA, "Nivya, that will be all for today, thanks")
        await call.answered()
        assert call.gate.dormant
        call.gate.heard(ASHA, "and tomorrow?")

    stub = run_call(script, calls=["go_quiet"])
    assert len(stub.requests) == 1
    assert stub.offered[0] == ["current_time", "go_quiet", "who_is_here"]


def test_state_events_say_whether_it_is_dormant_and_who_woke_it() -> None:
    states: list[dict[str, Any]] = []

    async def script(call: Call, stub: StubLLM) -> None:
        call.gate.heard(ASHA, "Nivya, what time is it?")
        await call.answered()
        call.scheduler.fire()
        states.extend(await call.states())

    run_call(script)
    awake = {"dormant": False, "wokenBy": ASHA, "wokenVia": "name"}
    assert states == [
        {"state": "listening", "dormant": True},
        {"state": "listening", **awake},
        {"state": "thinking", "previousState": "listening", **awake},
        {"state": "speaking", "previousState": "thinking", **awake},
        {"state": "listening", "previousState": "speaking", **awake},
        {"state": "listening", "dormant": True},
    ]


def test_the_wake_control_counts_as_being_called_by_that_person() -> None:
    states: list[dict[str, Any]] = []

    async def script(call: Call, stub: StubLLM) -> None:
        call.gate.wake(RAVI)
        await call.answered()
        states.extend(await call.states())

    stub = run_call(script)
    assert said(stub.requests[0])[-1] == ("user", "[Speaker 2, to you] Nivya")
    assert {"state": "listening", "dormant": False, "wokenBy": RAVI, "wokenVia": "manual"} in states


def test_only_a_well_formed_wake_command_is_read() -> None:
    assert command(b'{"action": "wake"}') == "wake"
    for data in (b"wake", b'{"action": "sleep"}', b'{"action": "wake", "as": "p_1"}', b"[]"):
        assert command(data) is None


def test_an_awake_agent_may_stay_silent_on_a_line_to_the_room_but_never_on_her_name() -> None:
    async def script(call: Call, stub: StubLLM) -> None:
        call.gate.heard(ASHA, "Nivya, what time is it?")
        await call.answered()
        call.gate.heard(RAVI, "Asha, are you coming on Saturday?")
        await call.answered()
        assert not call.gate.dormant
        call.gate.heard(ASHA, "what time does it start?")
        await call.answered()

    stub = run_call(script, calls=["", "stay_silent"], stays_awake=True)
    assert len(stub.requests) == 3
    assert "stay_silent" not in stub.offered[0]
    assert "stay_silent" in stub.offered[1]
    asked = said(stub.requests[2])
    assert asked[-2:] == [
        ("user", "[Speaker 2, to the room] Asha, are you coming on Saturday?"),
        ("user", "[Speaker 1, to the room] what time does it start?"),
    ]
    assert not any(
        isinstance(item, llm.FunctionCall | llm.FunctionCallOutput)
        for item in stub.requests[2].items
    ), "a silence leaves no tool call or empty answer behind"
