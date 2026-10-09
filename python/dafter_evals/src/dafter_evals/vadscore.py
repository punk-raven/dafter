from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import numpy as np
from dafter_core.config import ProviderRef
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError
from dafter_providers import vendor_for
from livekit import rtc
from livekit.agents import vad

from .conditions import LEVEL_FRAME_MS, WORK_RATE, Audio, active_frames, at_rate

PUSH_FRAME_MS = 10
HANGOVER_MS = 250
FPR_TARGETS = (0.01, 0.05)
ACTIVATION_THRESHOLDS = (0.3, 0.5, 0.7)
LABEL_MAJORITY = 0.5
INFERENCE_TIMEOUT_S = 60.0
SECONDS_PER_HOUR = 3600.0
DECIMALS = 4


@dataclass(frozen=True, slots=True)
class Window:
    start_s: float
    end_s: float
    probability: float


@dataclass(frozen=True, slots=True, eq=False)
class ScoredClip:
    clip_id: str
    labels: np.ndarray
    scores: np.ndarray
    window_s: float


def reference_labels(audio: Audio, windows: list[Window]) -> np.ndarray:
    active = active_frames(audio.samples, audio.rate)
    frame_s = LEVEL_FRAME_MS / 1000
    labels = np.zeros(len(windows), dtype=bool)
    for i, window in enumerate(windows):
        first = int(window.start_s / frame_s)
        last = max(first + 1, round(window.end_s / frame_s))
        covered = active[first:last]
        labels[i] = covered.size > 0 and covered.mean() >= LABEL_MAJORITY
    return labels


def scored_clip(clip_id: str, reference: Audio, windows: list[Window]) -> ScoredClip:
    spans = [w.end_s - w.start_s for w in windows]
    return ScoredClip(
        clip_id=clip_id,
        labels=reference_labels(reference, windows),
        scores=np.array([w.probability for w in windows], dtype=np.float64),
        window_s=float(np.median(spans)) if spans else 0.0,
    )


def _ranks(scores: np.ndarray) -> np.ndarray:
    _, inverse, counts = np.unique(scores, return_inverse=True, return_counts=True)
    ends = np.cumsum(counts)
    average = ends - (counts - 1) / 2
    ranked: np.ndarray = average[inverse]
    return ranked


def roc_auc(labels: np.ndarray, scores: np.ndarray) -> float | None:
    positives = int(labels.sum())
    negatives = labels.size - positives
    if positives == 0 or negatives == 0:
        return None
    rank_sum = float(_ranks(scores)[labels].sum())
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def _operating_points(
    labels: np.ndarray, scores: np.ndarray
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    order = np.argsort(-scores, kind="mergesort")
    ordered_scores, ordered_labels = scores[order], labels[order]
    true_positives = np.cumsum(ordered_labels)
    false_positives = np.cumsum(~ordered_labels)
    last_of_tie = np.r_[np.diff(ordered_scores) != 0, True]
    return (
        true_positives[last_of_tie],
        false_positives[last_of_tie],
        ordered_scores[last_of_tie],
    )


def pr_auc(labels: np.ndarray, scores: np.ndarray) -> float | None:
    positives = int(labels.sum())
    if positives == 0 or labels.size == 0:
        return None
    true_positives, false_positives, _ = _operating_points(labels, scores)
    recall = true_positives / positives
    precision = true_positives / (true_positives + false_positives)
    return float(np.sum(np.diff(np.r_[0.0, recall]) * precision))


def tpr_at_fpr(labels: np.ndarray, scores: np.ndarray, target: float) -> float | None:
    positives = int(labels.sum())
    negatives = labels.size - positives
    if positives == 0 or negatives == 0:
        return None
    true_positives, false_positives, _ = _operating_points(labels, scores)
    allowed = false_positives / negatives <= target
    if not allowed.any():
        return 0.0
    return float((true_positives[allowed] / positives).max())


def cutoffs(labels: np.ndarray, scores: np.ndarray, threshold: float, hangover_windows: int) -> int:
    speaking = False
    silent_run = 0
    count = 0
    for i, probability in enumerate(scores):
        if probability >= threshold:
            speaking, silent_run = True, 0
            continue
        if not speaking:
            continue
        silent_run += 1
        if silent_run >= hangover_windows:
            speaking, silent_run = False, 0
            if labels[i - hangover_windows + 1 : i + 1].any():
                count += 1
    return count


def false_starts(labels: np.ndarray, scores: np.ndarray, threshold: float) -> int:
    active = scores >= threshold
    onsets = active & ~np.r_[False, active[:-1]]
    return int((onsets & ~labels).sum())


def _per_hour(count: int, hours: float) -> float | None:
    return _rounded(count / hours) if hours > 0 else None


def threshold_sweep(
    clips: list[ScoredClip], thresholds: tuple[float, ...] = ACTIVATION_THRESHOLDS
) -> dict[str, dict[str, float | None]]:
    hours = sum(c.labels.size * c.window_s for c in clips) / SECONDS_PER_HOUR
    swept: dict[str, dict[str, float | None]] = {"cutoffsPerHour": {}, "falseStartsPerHour": {}}
    for threshold in thresholds:
        key = f"{threshold:g}"
        cut = sum(
            cutoffs(c.labels, c.scores, threshold, _hangover_windows(c.window_s)) for c in clips
        )
        started = sum(false_starts(c.labels, c.scores, threshold) for c in clips)
        swept["cutoffsPerHour"][key] = _per_hour(cut, hours)
        swept["falseStartsPerHour"][key] = _per_hour(started, hours)
    return swept


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, DECIMALS)


def summarize(
    clips: list[ScoredClip], thresholds: tuple[float, ...] = ACTIVATION_THRESHOLDS
) -> dict[str, Any]:
    if not clips:
        return {"clips": 0}
    labels = np.concatenate([c.labels for c in clips])
    scores = np.concatenate([c.scores for c in clips])
    return {
        "clips": len(clips),
        "windows": int(labels.size),
        "speechShare": _rounded(float(labels.mean())) if labels.size else None,
        "rocAuc": _rounded(roc_auc(labels, scores)),
        "prAuc": _rounded(pr_auc(labels, scores)),
        "tprAtFpr": {
            f"{target:.0%}": _rounded(tpr_at_fpr(labels, scores, target)) for target in FPR_TARGETS
        },
        **threshold_sweep(clips, thresholds),
        "hangoverMs": HANGOVER_MS,
        "labelSource": "frame energy of the clip before its condition",
    }


def _hangover_windows(window_s: float) -> int:
    if window_s <= 0:
        return 1
    return max(1, round(HANGOVER_MS / 1000 / window_s))


def detector(ref: ProviderRef | None = None) -> vad.VAD:
    chosen = ref or ProviderRef(provider="silero", model="silero")
    build = vendor_for(chosen, Stage.VAD).vad
    if build is None:
        raise DafterError(ErrorCode.UNSUPPORTED_CAPABILITY, f"{chosen.provider} has no vad")
    return build(chosen)


async def speech_probabilities(audio: Audio, model: vad.VAD) -> list[Window]:
    pcm = at_rate(audio, WORK_RATE).samples
    stream = model.stream()
    windows: list[Window] = []

    async def listen() -> None:
        cursor = 0.0
        async for event in stream:
            if event.type is not vad.VADEventType.INFERENCE_DONE:
                continue
            span = sum(f.samples_per_channel / f.sample_rate for f in event.frames)
            windows.append(Window(cursor, cursor + span, float(event.probability)))
            cursor += span

    listening = asyncio.ensure_future(listen())
    step = WORK_RATE * PUSH_FRAME_MS // 1000
    try:
        for start in range(0, pcm.size, step):
            chunk = pcm[start : start + step]
            stream.push_frame(rtc.AudioFrame(chunk.tobytes(), WORK_RATE, 1, chunk.size))
        stream.end_input()
        await asyncio.wait_for(listening, INFERENCE_TIMEOUT_S)
    finally:
        if not listening.done():
            listening.cancel()
        await stream.aclose()
    return windows
