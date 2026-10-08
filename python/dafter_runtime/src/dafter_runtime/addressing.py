from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from dafter_core.enums import WakeSource

from .naming import Heard, Matcher
from .noise import Meaning

log = logging.getLogger("dafter.runtime.addressing")

BUSY_STATES = frozenset({"thinking", "speaking"})
FLOOR_S = 10.0
REPAIR_S = 6.0

Timing = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Said:
    speaker: str
    text: str
    at: float


class Responder(Protocol):
    def answer(
        self,
        speaker: str,
        text: str,
        overheard: list[Said],
        timing: Timing | None,
        judged: bool = False,
    ) -> None: ...

    def hush(self) -> None: ...

    def addressed(self, woken_by: str | None, via: WakeSource | None) -> None: ...


class Timer(Protocol):
    def cancel(self) -> None: ...


Clock = Callable[[], float]
Schedule = Callable[[float, Callable[[], None]], Timer]


class Route(Enum):
    ANSWER = "answer"
    JUDGE = "judge"
    KEEP = "keep"


def nobody_counted() -> int:
    return 0


def never_echoed(speaker: str) -> bool:
    return False


class Gate:
    def __init__(
        self,
        matcher: Matcher,
        follow_up_s: float,
        responder: Responder,
        clock: Clock,
        schedule: Schedule,
        name: str = "",
        meaning: Meaning | None = None,
        busy_words: int = 1,
        stays_awake: bool = False,
        present: Callable[[], int] = nobody_counted,
        echoed: Callable[[str], bool] = never_echoed,
    ) -> None:
        self._matcher = matcher
        self._name = name
        self._meaning = meaning
        self._busy_words = max(busy_words, 1)
        self._follow_up_s = follow_up_s
        self._responder = responder
        self._clock = clock
        self._schedule = schedule
        self._stays_awake = stays_awake
        self._awake = False
        self._addressee: str | None = None
        self._busy = False
        self._quiet_after_reply = False
        self._timer: Timer | None = None
        self._said: list[Said] = []
        self._present = present
        self._echoed = echoed
        self._state = "listening"
        self._asked = False
        self._finished_at: float | None = None
        self._answering: str | None = None
        self._talking_to: str | None = None
        self._since: set[str] = set()
        self._judging: str | None = None
        self._refused: tuple[str, float] | None = None

    @property
    def dormant(self) -> bool:
        return not self._awake

    @property
    def addressee(self) -> str | None:
        return self._addressee

    def people(self) -> int:
        return self._present()

    def heard(self, speaker: str, text: str, timing: Timing | None = None) -> None:
        if self._says_something(text):
            self._since.add(speaker)
        heard = self._matcher.hear(text)
        if heard is Heard.STOPPED or (heard is Heard.STOP and not self.dormant):
            self.sleep()
            self._responder.hush()
            return
        if heard is Heard.CALLED:
            self._wake(speaker, WakeSource.NAME)
            self._answer(speaker, text, timing)
            return
        if self._idle(text):
            return
        if self.dormant:
            self._overheard(speaker, text)
        elif self._stays_awake:
            self._judge(speaker, text, timing)
        elif speaker == self._addressee:
            self._answer(speaker, text, timing)
        else:
            self._overheard(speaker, text)
            if self._says_something(text):
                log.info("someone else spoke, the follow-up window closes")
                self.go_quiet()

    def _overheard(self, speaker: str, text: str) -> None:
        self._said.append(Said(speaker, text, self._clock()))

    def _judge(self, speaker: str, text: str, timing: Timing | None) -> None:
        if self._busy:
            if speaker == self._addressee:
                self._answer(speaker, text, timing)
            else:
                self._overheard(speaker, text)
            return
        route = self._route(speaker, text)
        if route is Route.ANSWER:
            self._answer(speaker, text, timing)
        elif route is Route.JUDGE:
            self._answer(speaker, text, timing, judged=True)
        else:
            self._overheard(speaker, text)

    def _route(self, speaker: str, text: str) -> Route:
        bare = self._meaning is not None and self._meaning.acknowledges(text)
        if self._present() == 1 and not self._echoed(speaker):
            if not bare or self._asked:
                return Route.ANSWER
            log.info("a bare acknowledgement in a one-to-one call is left unanswered")
            return Route.KEEP
        if self._holds_floor(speaker):
            log.info("an answer to her question is answered without a verdict")
            return Route.ANSWER
        if self._repairs(speaker):
            log.info("a line said again after a verdict of no is answered")
            self._refused = None
            return Route.ANSWER
        return Route.JUDGE

    def _holds_floor(self, speaker: str) -> bool:
        if not self._asked or speaker != self._talking_to or self._finished_at is None:
            return False
        recent = self._clock() - self._finished_at <= FLOOR_S
        return recent and not (self._since - {speaker})

    def _repairs(self, speaker: str) -> bool:
        if self._refused is None:
            return False
        refused, at = self._refused
        return refused == speaker and self._clock() - at <= REPAIR_S

    def verdict(self, meant: bool) -> None:
        judged, self._judging = self._judging, None
        self._refused = None if meant or judged is None else (judged, self._clock())

    def replied(self, asked: bool) -> None:
        self._asked = asked

    def _says_something(self, text: str) -> bool:
        return self._meaning is None or bool(self._meaning.words(text))

    def _idle(self, text: str) -> bool:
        if self._meaning is None:
            return False
        said = len(self._meaning.words(text))
        if self._busy:
            return said < self._busy_words
        return said == 0 and not self._meaning.acknowledges(text)

    def wake(self, speaker: str) -> None:
        self._wake(speaker, WakeSource.MANUAL)
        self._answer(speaker, self._name, None)

    def go_quiet(self) -> None:
        if self._busy and not self.dormant:
            self._quiet_after_reply = True
            return
        self.sleep()

    def sleep(self) -> None:
        self._stop_timer()
        self._quiet_after_reply = False
        if not self._awake:
            return
        self._awake = False
        self._addressee = None
        self._responder.addressed(None, None)

    def left(self, speaker: str) -> None:
        if speaker != self._addressee:
            return
        if self._stays_awake:
            self._addressee = None
        else:
            self.sleep()

    def agent_state(self, state: str) -> None:
        if self._state == "speaking" and state != "speaking":
            self._finished_at = self._clock()
            self._talking_to = self._answering
            self._since = set()
        self._state = state
        self._busy = state in BUSY_STATES
        if self._busy:
            self._stop_timer()
        elif self._quiet_after_reply:
            self.sleep()
        elif not self.dormant:
            self._start_timer()

    def _wake(self, speaker: str, via: WakeSource) -> None:
        if self._awake and speaker == self._addressee:
            return
        self._awake = True
        self._addressee = speaker
        self._responder.addressed(speaker, via)

    def _answer(self, speaker: str, text: str, timing: Timing | None, judged: bool = False) -> None:
        overheard, self._said = self._said, []
        self._quiet_after_reply = False
        self._answering = speaker
        self._judging = speaker if judged else None
        self._start_timer()
        self._responder.answer(speaker, text, overheard, timing, judged)

    def _start_timer(self) -> None:
        self._stop_timer()
        if not self._stays_awake:
            self._timer = self._schedule(self._follow_up_s, self._window_closed)

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _window_closed(self) -> None:
        self._timer = None
        if not self._busy:
            self.sleep()


__all__ = [
    "FLOOR_S",
    "REPAIR_S",
    "Gate",
    "Responder",
    "Route",
    "Said",
    "Timer",
    "Timing",
    "never_echoed",
    "nobody_counted",
]
