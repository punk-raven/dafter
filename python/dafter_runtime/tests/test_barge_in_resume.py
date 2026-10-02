from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

from dafter_runtime.answering import Roster, Voice
from dafter_runtime.barge_in import BargeIn, Resume

ASHA = "p_4b81e0d7"
RAVI = "p_9d02c3aa"
MIN_S = 0.25
RESUME_S = 2.0


class Pending:
    def __init__(self, delay: float, callback: Callable[[], None]) -> None:
        self.delay = delay
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class Rig:
    def __init__(self, resumes: bool = True, waits_for_words: bool = True) -> None:
        self.caller: str | None = ASHA
        self.log: list[str] = []
        self.timers: list[Pending] = []
        resume = Resume(self._pause, lambda: self.log.append("resume"), RESUME_S)
        self.barge_in = BargeIn(
            MIN_S,
            0,
            lambda: self.caller,
            lambda: self.log.append("stop"),
            lambda: 1000.0,
            self._schedule,
            resume=resume if resumes else None,
            waits_for_words=waits_for_words,
        )

    def _pause(self) -> bool:
        self.log.append("pause")
        return True

    def _schedule(self, delay: float, callback: Callable[[], None]) -> Pending:
        self.timers.append(Pending(delay, callback))
        return self.timers[-1]

    def fire(self, delay: float) -> None:
        for timer in [t for t in self.timers if not t.cancelled and t.delay == delay]:
            timer.cancelled = True
            timer.callback()

    def talks_over(self, speaker: str = ASHA) -> None:
        self.barge_in.speaking(speaker, 1000.0)
        self.fire(MIN_S)


def test_talking_over_the_reply_pauses_it_at_once_and_words_then_cut_it() -> None:
    rig = Rig()
    rig.talks_over()
    assert rig.log == ["pause"]
    rig.barge_in.transcribed(ASHA, "रुकिए", final=False)
    rig.barge_in.quiet(ASHA)
    rig.fire(RESUME_S)
    assert rig.log == ["pause", "stop"]


def test_a_final_after_the_caller_went_quiet_still_cuts_the_paused_reply() -> None:
    rig = Rig()
    rig.talks_over()
    rig.barge_in.quiet(ASHA)
    rig.barge_in.transcribed(ASHA, "एक सवाल है", final=True)
    rig.fire(RESUME_S)
    assert rig.log == ["pause", "stop"]


def test_an_acknowledgement_the_listener_dropped_resumes_the_reply_at_once() -> None:
    rig = Rig()
    rig.talks_over()
    rig.barge_in.quiet(ASHA)
    rig.barge_in.acknowledged(ASHA)
    assert rig.log == ["pause", "resume"]
    rig.fire(RESUME_S)
    assert rig.log == ["pause", "resume"]


def test_nothing_heard_resumes_the_reply_after_the_false_interruption_timeout() -> None:
    rig = Rig()
    rig.talks_over()
    rig.barge_in.quiet(ASHA)
    assert rig.log == ["pause"]
    rig.fire(RESUME_S)
    assert rig.log == ["pause", "resume"]


def test_talking_again_before_the_timeout_keeps_the_reply_paused() -> None:
    rig = Rig()
    rig.talks_over()
    rig.barge_in.quiet(ASHA)
    rig.barge_in.speaking(ASHA, 1000.0)
    rig.fire(RESUME_S)
    assert rig.log == ["pause"]
    rig.barge_in.transcribed(ASHA, "सुनिए", final=False)
    assert rig.log == ["pause", "stop"]


def test_someone_else_neither_pauses_nor_resumes_nor_cuts_the_reply() -> None:
    rig = Rig()
    rig.talks_over(RAVI)
    rig.barge_in.transcribed(RAVI, "रुको", final=False)
    rig.barge_in.quiet(RAVI)
    rig.barge_in.acknowledged(RAVI)
    rig.fire(RESUME_S)
    assert rig.log == []


def test_without_resume_acknowledgements_hold_the_cut_until_a_word_is_heard() -> None:
    rig = Rig(resumes=False)
    rig.talks_over()
    assert rig.log == []
    rig.barge_in.transcribed(ASHA, "रुकिए", final=False)
    assert rig.log == ["stop"]


def test_without_resume_or_acknowledgements_the_minimum_alone_cuts_as_in_stage_3() -> None:
    rig = Rig(resumes=False, waits_for_words=False)
    rig.talks_over()
    assert rig.log == ["stop"]


@dataclass
class Speech:
    interrupted: bool = False
    allow_interruptions: bool = True
    done_callbacks: list[Callable[[Any], None]] = field(default_factory=list)

    def interrupt(self, source: str) -> None:
        self.interrupted = True

    def add_done_callback(self, callback: Callable[[Any], None]) -> None:
        self.done_callbacks.append(callback)


@dataclass
class Output:
    can_pause: bool = True
    paused: bool = False

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False


@dataclass
class Outputs:
    audio: Output | None = field(default_factory=Output)


@dataclass
class Session:
    current_speech: Speech | None = field(default_factory=Speech)
    output: Outputs = field(default_factory=Outputs)


def voice(session: Session, interruptible: bool = True) -> Voice:
    return Voice(cast(Any, session), Roster(), interruptible=interruptible)


def test_the_voice_pauses_and_resumes_its_room_output_around_an_interruptible_reply() -> None:
    session = Session()
    v = voice(session)
    assert v.pause()
    assert session.output.audio is not None and session.output.audio.paused
    v.resume()
    assert not session.output.audio.paused


def test_a_cut_reply_releases_the_paused_output_once_it_is_done() -> None:
    session = Session()
    v = voice(session)
    v.pause()
    v.barge_in()
    speech = session.current_speech
    assert speech is not None and speech.interrupted
    assert session.output.audio is not None and session.output.audio.paused
    for done in speech.done_callbacks:
        done(speech)
    assert not session.output.audio.paused


def test_nothing_to_pause_is_not_paused() -> None:
    assert not voice(Session(current_speech=None)).pause()
    assert not voice(Session(), interruptible=False).pause()
    assert not voice(Session(current_speech=Speech(allow_interruptions=False))).pause()
    assert not voice(Session(output=Outputs(audio=Output(can_pause=False)))).pause()
    assert not voice(Session(output=Outputs(audio=None))).pause()
