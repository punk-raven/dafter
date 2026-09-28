from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from dafter_core.enums import WakeSource

from .naming import Heard, Matcher

CONTEXT_SECONDS = 90.0
CONTEXT_LINES = 12
BUSY_STATES = frozenset({"thinking", "speaking"})

Timing = Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Said:
    speaker: str
    text: str
    at: float


class Responder(Protocol):
    def answer(
        self, speaker: str, text: str, overheard: list[Said], timing: Timing | None
    ) -> None: ...

    def hush(self) -> None: ...

    def addressed(self, woken_by: str | None, via: WakeSource | None) -> None: ...


class Timer(Protocol):
    def cancel(self) -> None: ...


Clock = Callable[[], float]
Schedule = Callable[[float, Callable[[], None]], Timer]


class Gate:
    def __init__(
        self,
        matcher: Matcher,
        follow_up_s: float,
        responder: Responder,
        clock: Clock,
        schedule: Schedule,
        name: str = "",
    ) -> None:
        self._matcher = matcher
        self._name = name
        self._follow_up_s = follow_up_s
        self._responder = responder
        self._clock = clock
        self._schedule = schedule
        self._addressee: str | None = None
        self._busy = False
        self._timer: Timer | None = None
        self._said: deque[Said] = deque(maxlen=CONTEXT_LINES)

    @property
    def dormant(self) -> bool:
        return self._addressee is None

    @property
    def addressee(self) -> str | None:
        return self._addressee

    def heard(self, speaker: str, text: str, timing: Timing | None = None) -> None:
        heard = self._matcher.hear(text)
        if heard is Heard.STOPPED or (heard is Heard.STOP and not self.dormant):
            self.sleep()
            self._responder.hush()
            return
        if heard is Heard.CALLED:
            self._wake(speaker, WakeSource.NAME)
            self._answer(speaker, text, timing)
            return
        if speaker == self._addressee:
            self._answer(speaker, text, timing)
            return
        self._said.append(Said(speaker, text, self._clock()))

    def wake(self, speaker: str) -> None:
        self._wake(speaker, WakeSource.MANUAL)
        self._answer(speaker, self._name, None)

    def sleep(self) -> None:
        self._stop_timer()
        if self._addressee is None:
            return
        self._addressee = None
        self._responder.addressed(None, None)

    def left(self, speaker: str) -> None:
        if speaker == self._addressee:
            self.sleep()

    def agent_state(self, state: str) -> None:
        self._busy = state in BUSY_STATES
        if self._busy:
            self._stop_timer()
        elif not self.dormant:
            self._start_timer()

    def _wake(self, speaker: str, via: WakeSource) -> None:
        if speaker == self._addressee:
            return
        self._addressee = speaker
        self._responder.addressed(speaker, via)

    def _answer(self, speaker: str, text: str, timing: Timing | None) -> None:
        since = self._clock() - CONTEXT_SECONDS
        overheard = [s for s in self._said if s.at >= since]
        self._said.clear()
        self._start_timer()
        self._responder.answer(speaker, text, overheard, timing)

    def _start_timer(self) -> None:
        self._stop_timer()
        self._timer = self._schedule(self._follow_up_s, self._window_closed)

    def _stop_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _window_closed(self) -> None:
        self._timer = None
        if not self._busy:
            self.sleep()


__all__ = ["Gate", "Responder", "Said", "Timer", "Timing"]
