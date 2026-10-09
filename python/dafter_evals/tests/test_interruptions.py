from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path

import numpy as np
import pytest
from dafter_evals import interruptions
from dafter_evals.golden import GoldenClip
from dafter_evals.interruptions import (
    BACKCHANNEL,
    INTERRUPTION,
    InterruptionTrial,
    overlap_clips,
    overlap_label,
    replay,
    summarize,
)
from dafter_evals.probe import Meter, Utterance, now

FRAME_S = 0.01


def clip(clip_id: str, labels: tuple[str, ...]) -> GoldenClip:
    return GoldenClip(
        clip_id=clip_id,
        language="te-IN",
        audio_path=Path("audio/te-IN") / f"{clip_id}.wav",
        channel="telephony_8k",
        reference_native="సరే",
        reference_romanized=None,
        entities=(),
        labels=labels,
        consent_id="consent",
    )


def trial(
    kind: str,
    stopped: bool,
    stop_ms: int | None = None,
    resumed: bool = False,
    replied: bool = False,
    inconclusive: bool = False,
    label_class: str = "acknowledge",
) -> InterruptionTrial:
    return InterruptionTrial(
        "c", kind, label_class, stopped, stop_ms, resumed, replied, inconclusive
    )


class FakeProbe:
    def __init__(self, utterances: list[Utterance]) -> None:
        self.meter = Meter()
        self.states: list[tuple[float, str]] = []
        self._utterances = deque(utterances)

    async def settle(self) -> bool:
        return True

    def speak(self, pcm: np.ndarray) -> asyncio.Future[Utterance]:
        done: asyncio.Future[Utterance] = asyncio.get_running_loop().create_future()
        done.set_result(self._utterances.popleft())
        return done

    def agent_says(self, start: float, end: float, level: float = 0.2) -> None:
        for t in np.arange(start, end, FRAME_S):
            self.meter.add(float(t), level)


def test_the_overlap_label_names_kind_and_class() -> None:
    assert overlap_label(clip("a", ("interruption:correction",))) == (INTERRUPTION, "correction")
    assert overlap_label(clip("b", ("codemix", "backchannel:acknowledge"))) == (
        BACKCHANNEL,
        "acknowledge",
    )
    assert overlap_label(clip("c", ("turn:end",))) is None
    assert [
        c.clip_id
        for c in overlap_clips([clip("c", ("turn:end",)), clip("a", ("interruption:stop",))])
    ] == ["a"]


def test_the_summary_scores_barge_ins_and_backchannels_apart() -> None:
    trials = [
        trial(INTERRUPTION, True, 200, label_class="correction"),
        trial(INTERRUPTION, True, 400, label_class="correction"),
        trial(INTERRUPTION, False, label_class="stop"),
        trial(INTERRUPTION, False, inconclusive=True),
        trial(BACKCHANNEL, False),
        trial(BACKCHANNEL, False, replied=True),
        trial(BACKCHANNEL, True, 300, resumed=True),
        trial(BACKCHANNEL, True, 300),
    ]
    s = summarize(trials)
    assert (s["trials"], s["inconclusive"], s["interruptions"], s["backchannels"]) == (8, 1, 3, 4)
    assert s["missedInterruptionRate"] == 0.3333
    assert s["falseBargeInRate"] == 0.5
    assert s["backchannelRecall"] == 0.5
    assert s["resumeSuccess"] == 0.5
    assert (s["tStopP50Ms"], s["tStopP95Ms"]) == (300, 390)
    assert s["classes"][INTERRUPTION] == {
        "correction": {"trials": 2, "stopped": 2},
        "stop": {"trials": 1, "stopped": 0},
    }
    assert len(s["trialsDetail"]) == 8


def test_an_empty_sweep_reports_no_rates() -> None:
    s = summarize([])
    assert s["missedInterruptionRate"] is None
    assert s["resumeSuccess"] is None


def test_replay_sees_a_backchannel_pause_and_resume() -> None:
    async def scenario() -> InterruptionTrial:
        base = now() - 10.0
        probe = FakeProbe(
            [
                Utterance(started=base, ended=base + 1.0),
                Utterance(started=base + 2.4, ended=base + 2.7),
            ]
        )
        probe.agent_says(base + 1.3, base + 2.6)
        probe.agent_says(base + 2.6, base + 3.5, level=0.0)
        probe.agent_says(base + 3.5, base + 5.0)
        heard = clip("b", ("backchannel:acknowledge",))
        prompt, pcm = np.zeros(10, dtype=np.int16), np.zeros(10, dtype=np.int16)
        return await replay(probe, heard, prompt, pcm)  # type: ignore[arg-type]

    result = asyncio.run(scenario())
    assert (result.kind, result.stopped, result.resumed, result.replied) == (
        BACKCHANNEL,
        True,
        True,
        False,
    )
    assert result.stop_ms == pytest.approx(200, abs=15)
    assert not result.inconclusive
    assert result.handled_as_backchannel


def test_replay_is_inconclusive_when_the_agent_never_answers_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(interruptions, "REPLY_TIMEOUT_S", 0.05)

    async def scenario() -> InterruptionTrial:
        base = now() - 30.0
        probe = FakeProbe([Utterance(started=base, ended=base + 1.0)])
        probe.agent_says(base - 5.0, base - 4.0)
        heard = clip("a", ("interruption:stop",))
        return await replay(probe, heard, np.zeros(1), np.zeros(1))  # type: ignore[arg-type]

    assert asyncio.run(scenario()).inconclusive
