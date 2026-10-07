from __future__ import annotations

from collections.abc import Callable

from dafter_core.enums import WakeSource
from dafter_runtime.addressing import Gate, Said, Timing
from dafter_runtime.naming import Matcher

ASHA = "p_4b81e0d7"
RAVI = "p_9d02c3aa"


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


class Responder:
    def __init__(self) -> None:
        self.answered: list[str] = []
        self.hushed = 0

    def answer(
        self,
        speaker: str,
        text: str,
        overheard: list[Said],
        timing: Timing | None,
        judged: bool = False,
    ) -> None:
        self.answered.append(text)

    def hush(self) -> None:
        self.hushed += 1

    def addressed(self, woken_by: str | None, via: WakeSource | None) -> None:
        pass


def gate() -> tuple[Gate, Responder]:
    responder = Responder()
    return Gate(Matcher("Nivya", (), ()), 20.0, responder, lambda: 0.0, Scheduler()), responder


def test_going_quiet_waits_for_the_reply_so_its_listener_can_still_stop_it() -> None:
    g, responder = gate()
    g.heard(ASHA, "Nivya, tell me a story")
    g.agent_state("thinking")
    g.go_quiet()
    g.agent_state("speaking")
    assert g.addressee == ASHA

    g.heard(ASHA, "stop")
    assert responder.hushed == 1
    assert g.dormant


def test_going_quiet_takes_effect_when_the_reply_ends() -> None:
    g, _ = gate()
    g.heard(ASHA, "Nivya, thanks a lot")
    g.agent_state("thinking")
    g.go_quiet()
    g.agent_state("speaking")
    assert not g.dormant
    g.agent_state("listening")
    assert g.dormant
    g.heard(ASHA, "and tomorrow?")
    g.agent_state("listening")
    assert g.dormant


def test_a_new_question_in_the_same_reply_cancels_going_quiet() -> None:
    g, responder = gate()
    g.heard(ASHA, "Nivya, thanks a lot")
    g.agent_state("thinking")
    g.go_quiet()
    g.heard(ASHA, "wait, one more thing")
    g.agent_state("listening")
    assert g.addressee == ASHA
    assert responder.answered == ["Nivya, thanks a lot", "wait, one more thing"]


def test_going_quiet_while_idle_is_at_once() -> None:
    g, _ = gate()
    g.heard(ASHA, "Nivya, hello")
    g.agent_state("listening")
    g.go_quiet()
    assert g.dormant
