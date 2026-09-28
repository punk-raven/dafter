from __future__ import annotations

import math
import time
from collections.abc import Callable
from typing import NamedTuple, TypeAlias
from weakref import WeakKeyDictionary

from livekit import rtc

VOICED_RMS = 0.02

AudioFrame: TypeAlias = rtc.AudioFrame


def voiced(frame: AudioFrame) -> bool:
    samples = frame.data
    if not samples:
        return False
    power = sum(s * s for s in samples) / len(samples)
    return math.sqrt(power) / 32768.0 >= VOICED_RMS


class Endpoint(NamedTuple):
    voiced_until: float
    released_at: float


class Endpoints:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._voiced_until: float | None = None
        self._last: Endpoint | None = None

    def heard(self, frame: AudioFrame) -> None:
        if voiced(frame):
            self._voiced_until = self._clock()

    def released(self) -> None:
        if self._voiced_until is None:
            return
        self._last = Endpoint(voiced_until=self._voiced_until, released_at=self._clock())
        self._voiced_until = None

    def take(self) -> Endpoint | None:
        last, self._last = self._last, None
        return last


_KEPT: WeakKeyDictionary[object, Endpoints] = WeakKeyDictionary()


def endpoints_of(model: object) -> Endpoints:
    found = _KEPT.get(model)
    if found is None:
        found = _KEPT[model] = Endpoints()
    return found
