from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from typing import Any

from dafter_core.config import Addressing
from dafter_core.enums import AddressingMode
from dafter_runtime.addressing import Gate, Timing
from dafter_runtime.answering import Roster, Voice
from dafter_runtime.naming import Matcher
from dafter_runtime.timing import Turns, TurnTiming
from livekit.agents import Agent, AgentSession, llm
from livekit.agents.voice.events import ConversationItemAddedEvent
from stub_llm import StubLLM

ASHA = "p_4b81e0d7"
ADDRESSING = Addressing(mode=AddressingMode.TRANSCRIPT, name="Nivya")


class Never:
    def cancel(self) -> None:
        return None


def never(delay: float, callback: Callable[[], None]) -> Never:
    return Never()


def called_turn(say: Callable[[Gate], None]) -> tuple[TurnTiming, list[llm.ChatMessage]]:
    rows: list[TurnTiming] = []
    users: list[llm.ChatMessage] = []

    async def run() -> None:
        turns = Turns()

        def added(ev: ConversationItemAddedEvent) -> None:
            if not isinstance(ev.item, llm.ChatMessage):
                return
            if ev.item.role == "user":
                users.append(ev.item)
            if (timing := turns.add(ev.item)) is not None:
                rows.append(timing)

        async with AgentSession[None](llm=StubLLM()) as session:
            session.on("conversation_item_added", added)
            await session.start(Agent(instructions=""))
            roster = Roster()
            roster.join(ASHA)
            voice = Voice(session, roster, interruptible=True)
            gate = Gate(
                Matcher.for_addressing(ADDRESSING),
                20.0,
                voice,
                clock=time.monotonic,
                schedule=never,
                name=ADDRESSING.name,
            )
            say(gate)
            assert voice.reply is not None
            await voice.reply
            await asyncio.sleep(0)

    asyncio.run(run())
    [row] = rows
    return row, users


def test_a_called_turn_keeps_the_callers_layers_and_its_reply_gap_from_their_speech() -> None:
    stopped = time.time() - 0.5
    heard: dict[str, Any] = {
        "started_speaking_at": stopped - 1.2,
        "stopped_speaking_at": stopped,
        "end_of_turn_delay": 0.35,
        "transcription_delay": 0.12,
        "on_user_turn_completed_delay": 0.001,
        "stt_metadata": {"model_name": "saaras:v3-realtime", "model_provider": "Sarvam"},
    }
    timing: Timing = heard

    row, [user] = called_turn(lambda gate: gate.heard(ASHA, "Nivya, what time is it?", timing))

    assert user.metrics == {
        "started_speaking_at": stopped - 1.2,
        "stopped_speaking_at": stopped,
        "end_of_turn_delay": 0.35,
        "transcription_delay": 0.12,
    }
    payload = row.payload()
    assert (payload["endOfTurnDelayMs"], payload["transcriptionDelayMs"]) == (350, 120)
    assert payload["e2eLatencyMs"] >= 500


def test_a_turn_nobody_spoke_carries_no_user_layers() -> None:
    row, [user] = called_turn(lambda gate: gate.wake(ASHA))
    assert user.metrics == {}
    assert "endOfTurnDelayMs" not in row.payload()
    assert "transcriptionDelayMs" not in row.payload()
    assert "e2eLatencyMs" not in row.payload()
