from __future__ import annotations

from stub_llm import StubLLM, said
from test_called import ASHA, RAVI, Call, run_call


def test_an_answer_to_her_question_is_answered_without_a_verdict() -> None:
    async def script(call: Call, stub: StubLLM) -> None:
        call.gate.heard(ASHA, "Nivya, what time is it?")
        await call.answered()
        call.gate.heard(ASHA, "I need help with my bill")
        await call.answered()

    stub = run_call(script, stays_awake=True)
    assert stub.judged == []
    assert said(stub.requests[1])[-1] == ("user", "[Speaker 1, to you] I need help with my bill")


def test_a_line_said_again_after_a_no_is_answered_with_both_lines_in_view() -> None:
    async def script(call: Call, stub: StubLLM) -> None:
        call.gate.heard(ASHA, "Nivya, what time is it?")
        await call.answered()
        call.gate.heard(RAVI, "what is the weather in Goa")
        await call.answered()
        call.clock.now += 3
        call.gate.heard(RAVI, "the weather in Goa this weekend")
        await call.answered()

    stub = run_call(script, stays_awake=True, verdicts=["NO"])
    assert len(stub.judged) == 1
    assert len(stub.requests) == 2
    assert said(stub.requests[1])[-2:] == [
        ("user", "[Speaker 2, to the room] what is the weather in Goa"),
        ("user", "[Speaker 2, to you] the weather in Goa this weekend"),
    ]
