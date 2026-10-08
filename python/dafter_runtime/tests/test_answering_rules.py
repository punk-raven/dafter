from __future__ import annotations

from collections.abc import Callable

from dafter_core.enums import WakeSource
from dafter_runtime.addressing import FLOOR_S, REPAIR_S, Gate, Said, Timing
from dafter_runtime.naming import Matcher
from dafter_runtime.noise import Meaning

ASHA = "p_4b81e0d7"
RAVI = "p_9d02c3aa"


class Never:
    def __call__(self, delay: float, callback: Callable[[], None]) -> Never:
        return self

    def cancel(self) -> None:
        return None


class Turns:
    def __init__(self) -> None:
        self.turns: list[tuple[str, str, bool, list[str]]] = []

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
        return None

    def addressed(self, woken_by: str | None, via: WakeSource | None) -> None:
        return None


class Call:
    def __init__(self, people: int, echoing: frozenset[str] = frozenset()) -> None:
        self.now = 100.0
        self.people = people
        self.turns = Turns()
        self.gate = Gate(
            Matcher("Nivya", (), ()),
            15.0,
            self.turns,
            lambda: self.now,
            Never(),
            name="Nivya",
            meaning=Meaning(("hmm", "okay", "ok")),
            stays_awake=True,
            present=lambda: self.people,
            echoed=lambda speaker: speaker in echoing,
        )

    def replies(self, asked: bool) -> None:
        self.gate.agent_state("thinking")
        self.gate.agent_state("speaking")
        self.gate.replied(asked)
        self.gate.agent_state("listening")

    def last(self) -> tuple[str, str, bool, list[str]]:
        return self.turns.turns[-1]


def called_by(speaker: str, people: int, asked: bool) -> Call:
    call = Call(people)
    call.gate.heard(speaker, "Nivya, what are we doing this weekend?")
    call.replies(asked)
    return call


def test_one_to_one_every_line_with_words_is_answered_without_a_verdict() -> None:
    call = called_by(ASHA, 1, asked=False)
    call.gate.heard(ASHA, "Goa ki plan undi")
    call.gate.heard(ASHA, "beach ki veldam anukuntunnam")
    judged = [t[2] for t in call.turns.turns]
    assert judged == [False, False, False]


def test_one_to_one_counts_the_people_on_every_line() -> None:
    call = called_by(ASHA, 2, asked=False)
    call.gate.heard(ASHA, "the trip is on Saturday")
    assert call.last()[2] is True
    call.people = 1
    call.gate.heard(ASHA, "the trip is on Saturday")
    assert call.last()[2] is False


def test_one_to_one_falls_back_to_the_verdict_once_the_microphone_returned_her_voice() -> None:
    call = Call(1, echoing=frozenset({ASHA}))
    call.gate.heard(ASHA, "Nivya, hello")
    call.replies(asked=False)
    call.gate.heard(ASHA, "the trip is on Saturday")
    assert call.last() == (ASHA, "the trip is on Saturday", True, [])


def test_two_people_still_go_to_the_verdict() -> None:
    call = called_by(ASHA, 2, asked=False)
    call.gate.heard(RAVI, "Asha, are you coming?")
    assert call.last() == (RAVI, "Asha, are you coming?", True, [])


def test_one_to_one_a_bare_acknowledgement_is_answered_only_after_her_question() -> None:
    call = called_by(ASHA, 1, asked=False)
    call.gate.heard(ASHA, "okay")
    assert len(call.turns.turns) == 1
    call.gate.heard(ASHA, "and Sunday?")
    assert call.last() == (ASHA, "and Sunday?", False, ["okay"])

    asked = called_by(ASHA, 1, asked=True)
    asked.gate.heard(ASHA, "okay")
    assert asked.last() == (ASHA, "okay", False, [])


def test_her_question_gives_the_floor_to_the_person_she_asked() -> None:
    call = called_by(ASHA, 3, asked=True)
    call.now += FLOOR_S - 1
    call.gate.heard(ASHA, "Goa, with Ravi")
    assert call.last() == (ASHA, "Goa, with Ravi", False, [])


def test_the_floor_closes_after_its_time() -> None:
    call = called_by(ASHA, 3, asked=True)
    call.now += FLOOR_S + 1
    call.gate.heard(ASHA, "Goa, with Ravi")
    assert call.last()[2] is True


def test_the_floor_closes_when_someone_else_speaks_first() -> None:
    call = called_by(ASHA, 3, asked=True)
    call.gate.heard(RAVI, "Asha, did you book the hotel?")
    call.gate.heard(ASHA, "yes, last night")
    assert [t[2] for t in call.turns.turns[1:]] == [True, True]


def test_the_floor_belongs_to_the_one_she_asked_and_only_after_a_question() -> None:
    call = called_by(ASHA, 3, asked=True)
    call.gate.heard(RAVI, "Goa, with Asha")
    assert call.last()[2] is True

    told = called_by(ASHA, 3, asked=False)
    told.gate.heard(ASHA, "Goa, with Ravi")
    assert told.last()[2] is True


def test_a_line_said_again_after_a_verdict_of_no_is_answered() -> None:
    call = called_by(ASHA, 3, asked=False)
    call.gate.heard(RAVI, "what is the weather in Goa")
    assert call.last()[2] is True
    call.gate.verdict(False)
    call.now += REPAIR_S - 1
    call.gate.heard(RAVI, "the weather in Goa this weekend")
    assert call.last() == (RAVI, "the weather in Goa this weekend", False, [])
    call.gate.heard(RAVI, "and on Sunday")
    assert call.last()[2] is True


def test_a_repair_is_only_for_the_same_speaker_and_only_soon_after() -> None:
    late = called_by(ASHA, 3, asked=False)
    late.gate.heard(RAVI, "what is the weather in Goa")
    late.gate.verdict(False)
    late.now += REPAIR_S + 1
    late.gate.heard(RAVI, "the weather in Goa this weekend")
    assert late.last()[2] is True

    other = called_by(ASHA, 3, asked=False)
    other.gate.heard(RAVI, "what is the weather in Goa")
    other.gate.verdict(False)
    other.gate.heard(ASHA, "the weather in Goa this weekend")
    assert other.last()[2] is True


def test_a_verdict_of_yes_leaves_nothing_to_repair() -> None:
    call = called_by(ASHA, 3, asked=False)
    call.gate.heard(RAVI, "what is the weather in Goa")
    call.gate.verdict(True)
    call.gate.heard(RAVI, "and on Sunday")
    assert call.last()[2] is True


def test_the_gate_reports_the_people_present_to_the_judge() -> None:
    assert Call(4).gate.people() == 4
