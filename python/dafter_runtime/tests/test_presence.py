from __future__ import annotations

from collections.abc import Callable

from dafter_runtime.presence import Alone


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


def test_the_agent_leaves_only_when_nobody_comes_back_in_time() -> None:
    left: list[bool] = []
    schedule = Scheduler()
    alone = Alone(lambda: left.append(True), schedule, 60.0)

    alone.emptied()
    alone.emptied()
    assert len(schedule.live()) == 1 and schedule.live()[0].delay == 60.0
    alone.joined()
    assert schedule.live() == [] and not alone.waiting

    alone.emptied()
    [timer] = schedule.live()
    timer.callback()
    assert left == [True]
    assert not alone.waiting
