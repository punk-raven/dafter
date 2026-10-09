from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import numpy as np
import pytest
from dafter_evals.conditions import WORK_RATE, Audio
from dafter_evals.vadscore import (
    HANGOVER_MS,
    ScoredClip,
    Window,
    cutoffs,
    false_starts,
    pr_auc,
    reference_labels,
    roc_auc,
    scored_clip,
    speech_probabilities,
    summarize,
    threshold_sweep,
    tpr_at_fpr,
)
from livekit import rtc
from livekit.agents import vad


def flags(*values: int) -> np.ndarray:
    return np.array(values, dtype=bool)


def scores(*values: float) -> np.ndarray:
    return np.array(values, dtype=np.float64)


def test_roc_auc_is_one_when_speech_always_scores_higher() -> None:
    assert roc_auc(flags(0, 0, 1, 1), scores(0.1, 0.2, 0.8, 0.9)) == 1.0
    assert roc_auc(flags(1, 1, 0, 0), scores(0.1, 0.2, 0.8, 0.9)) == 0.0


def test_roc_auc_counts_ties_as_half() -> None:
    assert roc_auc(flags(0, 1), scores(0.5, 0.5)) == 0.5
    assert roc_auc(flags(1, 1), scores(0.5, 0.6)) is None


def test_pr_auc_is_average_precision() -> None:
    assert pr_auc(flags(0, 0, 1, 1), scores(0.1, 0.2, 0.8, 0.9)) == 1.0
    assert pr_auc(flags(1, 0, 1), scores(0.9, 0.8, 0.7)) == pytest.approx((1 + 2 / 3) / 2)
    assert pr_auc(flags(0, 0), scores(0.1, 0.2)) is None


def test_tpr_at_fpr_takes_the_best_threshold_inside_the_false_alarm_budget() -> None:
    labels = flags(*([1] * 10 + [0] * 100))
    values = scores(*([0.9] * 8 + [0.3] * 2 + [0.95] + [0.5] * 4 + [0.1] * 95))
    assert tpr_at_fpr(labels, values, 0.005) == 0.0
    assert tpr_at_fpr(labels, values, 0.01) == 0.8
    assert tpr_at_fpr(labels, values, 0.05) == 1.0


def test_a_cutoff_is_an_end_of_speech_inside_labelled_speech() -> None:
    labels = flags(1, 1, 1, 1, 1, 1, 0, 0, 0, 0)
    early = scores(0.9, 0.9, 0.1, 0.1, 0.9, 0.9, 0.1, 0.1, 0.1, 0.1)
    assert cutoffs(labels, early, 0.5, 2) == 1
    assert cutoffs(labels, early, 0.5, 3) == 0
    clean = scores(0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.1, 0.1, 0.1, 0.1)
    assert cutoffs(labels, clean, 0.5, 2) == 0


def test_reference_labels_follow_the_clean_clip_energy() -> None:
    loud = np.full(WORK_RATE // 10, 8000, dtype=np.int16)
    quiet = np.zeros(WORK_RATE // 10, dtype=np.int16)
    audio = Audio("clip", np.concatenate([quiet, loud, quiet]), WORK_RATE)
    windows = [Window(i * 0.05, (i + 1) * 0.05, 0.0) for i in range(6)]
    assert reference_labels(audio, windows).tolist() == [False, False, True, True, False, False]


def test_the_summary_reports_every_metric_per_threshold() -> None:
    labels = flags(*([0] * 50 + [1] * 50))
    values = np.r_[np.full(50, 0.1), np.full(50, 0.9)]
    report = summarize([ScoredClip("a", labels, values, 0.032)], (0.5,))
    assert report["rocAuc"] == 1.0
    assert report["prAuc"] == 1.0
    assert report["tprAtFpr"] == {"1%": 1.0, "5%": 1.0}
    assert report["cutoffsPerHour"] == {"0.5": 0.0}
    assert report["falseStartsPerHour"] == {"0.5": 0.0}
    assert report["hangoverMs"] == HANGOVER_MS
    assert summarize([]) == {"clips": 0}


def test_cutoffs_per_hour_scale_by_audio_length() -> None:
    labels = flags(*([1] * 20))
    values = np.r_[np.full(5, 0.9), np.full(10, 0.1), np.full(5, 0.9)]
    report = summarize([ScoredClip("a", labels, values, 0.036)], (0.5,))
    assert report["cutoffsPerHour"]["0.5"] == pytest.approx(3600 / (20 * 0.036), rel=1e-3)


def test_a_false_start_is_an_onset_over_the_threshold_outside_labelled_speech() -> None:
    labels = flags(0, 0, 0, 0, 1, 1, 0, 0)
    values = scores(0.6, 0.6, 0.2, 0.4, 0.9, 0.9, 0.2, 0.2)
    assert false_starts(labels, values, 0.5) == 1
    assert false_starts(labels, values, 0.3) == 2
    assert false_starts(labels, values, 0.7) == 0


def test_the_threshold_sweep_trades_cutoffs_against_false_starts() -> None:
    labels = flags(*([0] * 10 + [1] * 10))
    values = np.r_[np.full(10, 0.4), np.full(5, 0.9), np.full(5, 0.6)]
    swept = threshold_sweep([ScoredClip("a", labels, values, 0.05)], (0.3, 0.5, 0.7))
    hour = 3600 / (20 * 0.05)
    assert swept["falseStartsPerHour"] == {
        "0.3": pytest.approx(hour, rel=1e-3),
        "0.5": 0.0,
        "0.7": 0.0,
    }
    assert swept["cutoffsPerHour"]["0.3"] == 0.0
    assert swept["cutoffsPerHour"]["0.7"] == pytest.approx(hour, rel=1e-3)
    assert threshold_sweep([], (0.5,)) == {
        "cutoffsPerHour": {"0.5": None},
        "falseStartsPerHour": {"0.5": None},
    }


class FakeStream:
    def __init__(self) -> None:
        self._frames: asyncio.Queue[rtc.AudioFrame | None] = asyncio.Queue()

    def push_frame(self, frame: rtc.AudioFrame) -> None:
        self._frames.put_nowait(frame)

    def end_input(self) -> None:
        self._frames.put_nowait(None)

    async def aclose(self) -> None:
        return None

    def __aiter__(self) -> AsyncIterator[vad.VADEvent]:
        return self._events()

    async def _events(self) -> AsyncIterator[vad.VADEvent]:
        while (frame := await self._frames.get()) is not None:
            level = float(np.abs(np.frombuffer(frame.data, dtype=np.int16)).mean())
            yield vad.VADEvent(
                type=vad.VADEventType.INFERENCE_DONE,
                samples_index=0,
                timestamp=0.0,
                speech_duration=0.0,
                silence_duration=0.0,
                frames=[frame],
                probability=1.0 if level > 100 else 0.0,
            )


class FakeModel:
    def stream(self) -> Any:
        return FakeStream()


def test_probabilities_are_laid_on_the_audio_timeline() -> None:
    loud = np.full(WORK_RATE // 50, 8000, dtype=np.int16)
    quiet = np.zeros(WORK_RATE // 50, dtype=np.int16)
    audio = Audio("clip", np.concatenate([quiet, loud]), WORK_RATE)
    windows = asyncio.run(speech_probabilities(audio, FakeModel()))  # type: ignore[arg-type]
    assert [w.probability for w in windows] == [0.0, 0.0, 1.0, 1.0]
    assert windows[-1].end_s == pytest.approx(0.04)
    clip = scored_clip("clip", audio, windows)
    assert clip.window_s == pytest.approx(0.01)
    assert clip.labels.tolist() == [False, False, True, True]
