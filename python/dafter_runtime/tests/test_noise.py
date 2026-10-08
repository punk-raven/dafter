from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from dafter_core.enums import WakeSource
from dafter_core.hashing import seal
from dafter_runtime.addressing import Gate, Said, Timing
from dafter_runtime.barge_in import BargeIn
from dafter_runtime.called import meaning_for
from dafter_runtime.naming import Matcher
from dafter_runtime.noise import Meaning
from dafter_runtime.plan import load, plan

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
ASHA = "p_4b81e0d7"
MEANING = Meaning(["hmm", "okay", "ok", "uh huh", "yeah", "right", "haan", "हाँ", "ठीक है", "हम्म"])


def test_noise_backchannels_and_one_letter_fragments_carry_no_words() -> None:
    for heard in ("", "  ", ".", "I.", "Anyway", "Anyway, anyway", "Hmm.", "Hmm, okay", "uh-huh"):
        assert MEANING.words(heard) == (), heard
    assert MEANING.words("Wait, I have a question") == ("wait", "have", "question")
    assert MEANING.words("हाँ, बताइए") == ("बताइए",)
    assert MEANING.words("ना") == ("ना",)


def test_an_acknowledgement_is_told_apart_from_noise() -> None:
    assert MEANING.acknowledges("Hmm.")
    assert MEANING.acknowledges("ठीक है")
    assert not MEANING.acknowledges("Anyway")
    assert not MEANING.acknowledges("I")
    assert not MEANING.acknowledges("Okay, tell me more")


def test_a_stop_phrase_is_a_stop_on_its_own() -> None:
    assert MEANING.stop("Stop.")
    assert MEANING.stop("बस करो")
    assert not MEANING.stop("stop the car near the gate")


def test_the_session_meaning_knows_every_configured_backchannel() -> None:
    sealed, _ = seal(json.dumps(json.loads(JOB.read_bytes())))
    meaning = meaning_for(plan(load(sealed), "dafter-py"))
    for heard in ("hmm", "okay", "uh huh", "haan", "achha", "ठीक है", "ಹೂಂ", "సరే"):
        assert meaning.acknowledges(heard), heard


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
        self.pending.append(Pending(delay, callback))
        return self.pending[-1]

    def fire(self) -> None:
        for timer in [t for t in self.pending if not t.cancelled]:
            timer.cancelled = True
            timer.callback()


class Rig:
    def __init__(self) -> None:
        self.stops = 0
        self.scheduler = Scheduler()
        self.clock = Clock()
        self.barge_in = BargeIn(
            0.5, 2, lambda: ASHA, self._stop, self.clock, self.scheduler, meaning=MEANING
        )

    def _stop(self) -> None:
        self.stops += 1

    def over_her(self, *heard: str) -> int:
        self.barge_in.speaking(ASHA, self.clock.now)
        self.scheduler.fire()
        for text in heard:
            self.barge_in.transcribed(ASHA, text, final=False)
        stops = self.stops
        self.barge_in.quiet(ASHA)
        self.barge_in.committed(ASHA)
        return stops


def test_background_noise_and_backchannels_never_cut_her() -> None:
    rig = Rig()
    assert rig.over_her() == 0
    assert rig.over_her("Anyway", "Anyway anyway") == 0
    assert rig.over_her("Hmm", "Hmm okay") == 0
    assert rig.over_her("I", "uh-huh") == 0
    assert rig.over_her("Okay", "Okay right yeah") == 0


def test_two_real_words_or_a_stop_cut_her() -> None:
    assert Rig().over_her("Wait", "Wait, I have") == 1
    assert Rig().over_her("Stop") == 1
    assert Rig().over_her("Hmm, wait wait") == 1


class Responder:
    def __init__(self) -> None:
        self.answered: list[str] = []

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
        return None

    def addressed(self, woken_by: str | None, via: WakeSource | None) -> None:
        return None


def gate() -> tuple[Gate, Responder]:
    responder = Responder()
    g = Gate(
        Matcher("Nivya", (), ()),
        20.0,
        responder,
        lambda: 0.0,
        Scheduler(),
        meaning=MEANING,
        busy_words=2,
    )
    return g, responder


def test_noise_is_never_a_question_to_answer() -> None:
    g, responder = gate()
    g.heard(ASHA, "Nivya, how do I file an FIR?")
    g.agent_state("listening")
    for noise in ("Anyway", "I.", "", ".", "Anyway, anyway"):
        g.heard(ASHA, noise)
    assert responder.answered == ["Nivya, how do I file an FIR?"]


def test_a_backchannel_while_she_answers_does_not_replace_her_answer() -> None:
    g, responder = gate()
    g.heard(ASHA, "Nivya, how do I file an FIR?")
    g.agent_state("speaking")
    g.heard(ASHA, "Hmm.")
    g.heard(ASHA, "Okay")
    assert responder.answered == ["Nivya, how do I file an FIR?"]
    g.heard(ASHA, "Wait, which police station?")
    assert responder.answered[-1] == "Wait, which police station?"


def test_a_backchannel_after_she_has_finished_is_still_a_reply() -> None:
    g, responder = gate()
    g.heard(ASHA, "Nivya, shall I go on?")
    g.agent_state("listening")
    g.heard(ASHA, "Okay")
    assert responder.answered[-1] == "Okay"


def test_a_stray_word_from_the_room_does_not_replace_her_answer_but_a_question_does() -> None:
    g, responder = gate()
    g.heard(ASHA, "Nivya, how do I file an FIR?")
    g.agent_state("speaking")
    for fragment in ("Traffic", "Ramayana", "Hello"):
        g.heard(ASHA, fragment)
    assert responder.answered == ["Nivya, how do I file an FIR?"]
    g.agent_state("listening")
    g.heard(ASHA, "Delhi")
    assert responder.answered[-1] == "Delhi"
