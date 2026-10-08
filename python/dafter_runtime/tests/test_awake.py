from __future__ import annotations

import asyncio
from collections.abc import Callable

from dafter_core.enums import WakeSource
from dafter_runtime.addressing import Gate, Said, Timing
from dafter_runtime.history import judged
from dafter_runtime.memory import EARLIER, Memory
from dafter_runtime.naming import Matcher
from dafter_runtime.noise import Meaning
from livekit.agents import llm

ASHA = "p_4b81e0d7"
RAVI = "p_9d02c3aa"


class Never:
    def __init__(self) -> None:
        self.scheduled = 0

    def __call__(self, delay: float, callback: Callable[[], None]) -> Never:
        self.scheduled += 1
        return self

    def cancel(self) -> None:
        return None


class Turns:
    def __init__(self) -> None:
        self.turns: list[tuple[str, str, bool, list[str]]] = []
        self.hushed = 0
        self.awake: list[str | None] = []

    def answer(
        self,
        speaker: str,
        text: str,
        overheard: list[Said],
        timing: Timing | None,
        judged: bool = False,
    ) -> None:
        self.turns.append((speaker, text, judged, [s.text for s in overheard]))

    def hush(self) -> None:
        self.hushed += 1

    def addressed(self, woken_by: str | None, via: WakeSource | None) -> None:
        self.awake.append(woken_by)


def awake_gate() -> tuple[Gate, Turns, Never]:
    turns, never = Turns(), Never()
    gate = Gate(
        Matcher("Nivya", ("నివ్య",), ()),
        15.0,
        turns,
        lambda: 0.0,
        never,
        name="Nivya",
        meaning=Meaning(("hmm", "okay")),
        busy_words=2,
        stays_awake=True,
    )
    return gate, turns, never


def test_once_called_she_stays_awake_with_no_window_until_told_to_sleep() -> None:
    gate, turns, never = awake_gate()
    gate.heard(ASHA, "Nivya, what time is it?")
    gate.agent_state("speaking")
    gate.agent_state("listening")
    assert never.scheduled == 0
    assert not gate.dormant
    gate.heard(ASHA, "Nivya, go to sleep now.")
    assert gate.dormant
    assert turns.hushed == 1
    gate.heard(ASHA, "and tomorrow?")
    assert [t[1] for t in turns.turns] == ["Nivya, what time is it?"]


def test_while_awake_every_line_from_anyone_is_judged_and_her_name_is_answered() -> None:
    gate, turns, _ = awake_gate()
    gate.heard(ASHA, "Nivya, what time is it?")
    gate.heard(RAVI, "Asha, are you coming on Saturday?")
    gate.heard(ASHA, "నివ్య, ఒక joke చెప్పు")
    assert turns.turns == [
        (ASHA, "Nivya, what time is it?", False, []),
        (RAVI, "Asha, are you coming on Saturday?", True, []),
        (ASHA, "నివ్య, ఒక joke చెప్పు", False, []),
    ]


def test_while_she_speaks_others_are_context_and_the_one_she_answers_can_cut_in() -> None:
    gate, turns, _ = awake_gate()
    gate.heard(ASHA, "Nivya, tell me a story")
    gate.agent_state("speaking")
    gate.heard(RAVI, "this one is long")
    gate.heard(ASHA, "wait, a different story please")
    gate.agent_state("listening")
    assert turns.turns[1] == (ASHA, "wait, a different story please", False, ["this one is long"])


def test_the_one_who_called_her_leaving_does_not_put_her_to_sleep() -> None:
    gate, turns, _ = awake_gate()
    gate.heard(ASHA, "Nivya, hello")
    gate.left(ASHA)
    assert not gate.dormant
    gate.heard(RAVI, "what is the capital of Japan?")
    assert turns.turns[-1] == (RAVI, "what is the capital of Japan?", True, [])


def test_asleep_she_keeps_every_line_for_when_she_is_called_again() -> None:
    gate, turns, _ = awake_gate()
    gate.heard(RAVI, "the trip is on Saturday")
    gate.heard(ASHA, "it costs five thousand")
    gate.heard(ASHA, "Nivya, when is the trip?")
    assert turns.turns == [
        (
            ASHA,
            "Nivya, when is the trip?",
            False,
            ["the trip is on Saturday", "it costs five thousand"],
        )
    ]


def text_of(item: llm.ChatItem) -> str:
    assert isinstance(item, llm.ChatMessage)
    return item.text_content or ""


def test_a_line_said_to_the_room_is_judged_and_one_said_to_her_is_not() -> None:
    ctx = llm.ChatContext()
    ctx.add_message(
        role="user", content="[Ravi, not to you] hi\n[Asha, to the room] are you coming?"
    )
    assert judged(ctx)
    ctx.add_message(role="user", content="[Asha, to you] Nivya, are you there?")
    assert not judged(ctx)


def test_a_long_call_is_folded_into_running_minutes_she_reads_with_her_instructions() -> None:
    prompts: list[str] = []

    async def summarise(prompt: str) -> str:
        prompts.append(prompt)
        return "Ravi said the Goa trip on Saturday costs five thousand rupees."

    async def run() -> llm.ChatContext:
        memory = Memory(summarise, agent="the agent")
        ctx = llm.ChatContext()
        ctx.add_message(role="system", content="You are Nivya.")
        ctx.add_message(
            role="user", content="[Ravi, to the room] the Goa trip on Saturday costs five thousand"
        )
        for n in range(80):
            ctx.add_message(role="user", content=f"[Asha, to the room] {n} " + "talk " * 80)
        memory.context(ctx)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        ctx.add_message(
            role="user", content="[Asha, to you] Nivya, what did Ravi say about the trip?"
        )
        return memory.context(ctx)

    kept = asyncio.run(run())
    assert "Goa trip on Saturday costs five thousand" in prompts[0]
    first = kept.items[0]
    assert isinstance(first, llm.ChatMessage) and first.role == "system"
    minutes = "Ravi said the Goa trip on Saturday costs five thousand rupees."
    assert first.text_content == f"You are Nivya.\n\n{EARLIER}\n{minutes}"
    assert all(
        "Goa" not in (i.text_content or "")
        for i in kept.items[1:]
        if isinstance(i, llm.ChatMessage)
    )
    assert text_of(kept.items[-1]) == "[Asha, to you] Nivya, what did Ravi say about the trip?"
