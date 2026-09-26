from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest
from dafter_runtime.plan import load, plan
from livekit.agents import Agent, AgentSession, RunResult

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
KEY_ENV = "SARVAM_API_KEY"

pytestmark = [
    pytest.mark.llm_judge,
    pytest.mark.skipif(not os.environ.get(KEY_ENV), reason=f"{KEY_ENV} is not set"),
]


@pytest.mark.parametrize(
    ("user_input", "intent"),
    [
        (
            "नमस्ते, आप कौन हैं?",
            "Greets the caller or says who it is, in Hindi written in Devanagari, "
            "in at most two short sentences, with no markdown, lists or emojis.",
        ),
        (
            "मुझे पच्चीस और सत्रह का जोड़ बताइए।",
            "Says that the sum is forty-two, in Hindi, writing the number as words "
            "rather than digits.",
        ),
    ],
)
def test_the_hindi_persona_holds_its_voice_rules(user_input: str, intent: str) -> None:
    p = plan(load(JOB.read_bytes().strip()), "dafter-py")
    if p.llm.llm is None or p.pipeline.llm is None:
        pytest.fail("the Hindi job plans no LLM")
    agent_llm = p.llm.llm(p.pipeline.llm)

    async def run() -> None:
        async with AgentSession[None](llm=agent_llm) as session:
            await session.start(Agent(instructions=p.persona.instructions))
            result: RunResult[None] = await session.run(user_input=user_input)
            await (
                result.expect.next_event()
                .is_message(role="assistant")
                .judge(agent_llm, intent=intent)
            )

    asyncio.run(run())
