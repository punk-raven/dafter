from __future__ import annotations

import math

import pytest
from dafter_providers.endpointing import VOICED_RMS, Endpoint, Endpoints, endpoints_of, voiced
from livekit import rtc


def frame(level: int, samples: int = 160) -> rtc.AudioFrame:
    return rtc.AudioFrame(level.to_bytes(2, "little", signed=True) * samples, 16000, 1, samples)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.parametrize(
    ("level", "loud"),
    [(0, False), (300, False), (math.ceil(VOICED_RMS * 32768), True), (-8000, True)],
)
def test_a_frame_is_voiced_at_the_level_the_evals_probe_calls_loud(level: int, loud: bool) -> None:
    assert voiced(frame(level)) is loud


def test_an_empty_frame_is_not_voiced() -> None:
    assert voiced(frame(8000, samples=0)) is False


def test_the_endpoint_runs_from_the_last_voiced_frame_to_the_release() -> None:
    clock = Clock()
    endpoints = Endpoints(clock)
    for at, level in [(1.0, 8000), (1.5, 8000), (2.0, 0), (2.4, 100)]:
        clock.now = at
        endpoints.heard(frame(level))
    clock.now = 2.6
    endpoints.released()
    assert endpoints.take() == Endpoint(voiced_until=1.5, released_at=2.6)
    assert endpoints.take() is None


def test_a_release_with_no_voice_since_the_last_one_measures_nothing() -> None:
    clock = Clock()
    endpoints = Endpoints(clock)
    clock.now = 1.0
    endpoints.heard(frame(8000))
    clock.now = 1.8
    endpoints.released()
    clock.now = 3.0
    endpoints.heard(frame(0))
    endpoints.released()
    assert endpoints.take() == Endpoint(voiced_until=1.0, released_at=1.8)


class Model:
    pass


def test_each_model_keeps_its_own_endpoints() -> None:
    one, other = Model(), Model()
    assert endpoints_of(one) is endpoints_of(one)
    assert endpoints_of(one) is not endpoints_of(other)
