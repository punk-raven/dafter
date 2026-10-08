from __future__ import annotations

from collections.abc import Callable

from .addressing import Schedule, Timer

ALONE_GRACE_S = 60.0


class Alone:
    def __init__(self, leave: Callable[[], None], schedule: Schedule, after: float) -> None:
        self._leave = leave
        self._schedule = schedule
        self._after = after
        self._timer: Timer | None = None

    @property
    def waiting(self) -> bool:
        return self._timer is not None

    def emptied(self) -> None:
        if self._timer is None:
            self._timer = self._schedule(self._after, self._left_alone)

    def joined(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _left_alone(self) -> None:
        self._timer = None
        self._leave()


__all__ = ["ALONE_GRACE_S", "Alone"]
