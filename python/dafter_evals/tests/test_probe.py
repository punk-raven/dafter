from __future__ import annotations

import numpy as np
from dafter_evals.probe import SAMPLE_RATE, SAMPLES_PER_FRAME, Echo, Returned


def test_the_agent_comes_back_into_the_microphone_late_and_quieter() -> None:
    delay_frames = 2
    delay_ms = delay_frames * 1000 * SAMPLES_PER_FRAME // SAMPLE_RATE
    returned = Returned(Echo(gain=0.5, delay_ms=delay_ms))
    agent = np.full(SAMPLES_PER_FRAME, 1000, dtype=np.int16)
    caller = np.full(SAMPLES_PER_FRAME, 100, dtype=np.int16)
    returned.heard(agent)
    heard = [returned.over(caller) for _ in range(delay_frames + 2)]
    assert [int(f[0]) for f in heard] == [100, 100, 600, 100]
    assert all(f.dtype == np.int16 and f.size == SAMPLES_PER_FRAME for f in heard)


def test_a_loud_echo_over_a_loud_caller_clips_instead_of_wrapping() -> None:
    returned = Returned(Echo(gain=1.0, delay_ms=0))
    returned.heard(np.full(SAMPLES_PER_FRAME, 30000, dtype=np.int16))
    mixed = returned.over(np.full(SAMPLES_PER_FRAME, 30000, dtype=np.int16))
    assert int(mixed.max()) == 32767
