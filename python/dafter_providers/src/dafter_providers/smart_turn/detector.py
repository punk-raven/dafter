from __future__ import annotations

import asyncio
import importlib
import logging
import time
import weakref
from collections.abc import Callable
from pathlib import Path

import numpy as np
import numpy.typing as npt
from livekit import rtc
from livekit.agents import utils
from livekit.agents.inference.eot.base import (
    TurnDetectorOptions,
    _BaseStreamingTurnDetector,
    _BaseStreamingTurnDetectorStream,
)
from livekit.agents.inference.eot.languages import ThresholdOptions, TurnDetectorModels
from livekit.agents.language import LanguageCode
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

log = logging.getLogger("dafter.providers.smart_turn")

MODEL = "smart-turn-v3.2"
PROVIDER = "pipecat"
SAMPLE_RATE = 16000
WINDOW_SECONDS = 8
WINDOW_SAMPLES = SAMPLE_RATE * WINDOW_SECONDS
INT16_FULL_SCALE = 32768.0
COMPLETE_AT = 0.5
COMPLETE_WHEN_UNSURE = 1.0
FRAMEWORK_THRESHOLDS_MODEL: TurnDetectorModels = "turn-detector-v1-mini"

Pcm = npt.NDArray[np.int16]
Waveform = npt.NDArray[np.float32]
Predict = Callable[[Pcm], float]


def last_window(waveform: Waveform) -> Waveform:
    if len(waveform) >= WINDOW_SAMPLES:
        return waveform[-WINDOW_SAMPLES:]
    return np.pad(waveform, (WINDOW_SAMPLES - len(waveform), 0)).astype(np.float32)


def waveform_of(pcm: Pcm) -> Waveform:
    return (pcm.astype(np.float32) / INT16_FULL_SCALE).astype(np.float32)


class OnnxEndpointModel:
    def __init__(self, weights: Path) -> None:
        onnxruntime = importlib.import_module("onnxruntime")
        transformers = importlib.import_module("transformers")
        options = onnxruntime.SessionOptions()
        options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
        options.inter_op_num_threads = 1
        options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = onnxruntime.InferenceSession(str(weights), sess_options=options)
        self._features = transformers.WhisperFeatureExtractor(chunk_length=WINDOW_SECONDS)

    def __call__(self, pcm: Pcm) -> float:
        extracted = self._features(
            last_window(waveform_of(pcm)),
            sampling_rate=SAMPLE_RATE,
            return_tensors="np",
            padding="max_length",
            max_length=WINDOW_SAMPLES,
            truncation=True,
            do_normalize=True,
        )
        features = np.expand_dims(extracted.input_features.squeeze(0).astype(np.float32), axis=0)
        outputs = self._session.run(None, {"input_features": features})
        return float(outputs[0][0].item())


def covers(language: LanguageCode | None, languages: frozenset[str]) -> bool:
    return language is None or language.language in languages


class WindowTransport:
    def __init__(self, predict: Predict) -> None:
        self._predict = predict
        self._window = utils.AudioArrayBuffer(buffer_size=WINDOW_SAMPLES, sample_rate=SAMPLE_RATE)
        self._stream: weakref.ref[_BaseStreamingTurnDetectorStream] | None = None
        self._tasks: set[asyncio.Task[None]] = set()

    @property
    def session_id(self) -> str | None:
        return None

    def attach(self, stream: _BaseStreamingTurnDetectorStream) -> None:
        self._stream = weakref.ref(stream)

    def detach(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()

    def run_inference(self, request_id: str) -> None:
        task = asyncio.create_task(self._resolve(request_id, self._window.read()))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _resolve(self, request_id: str, pcm: Pcm) -> None:
        started = time.monotonic()
        try:
            probability = float(await asyncio.to_thread(self._predict, pcm))
        except Exception:
            log.exception("smart turn prediction failed")
            probability = COMPLETE_WHEN_UNSURE
        stream = self._stream() if self._stream is not None else None
        if stream is None:
            return
        stream._resolve_prediction(
            request_id, probability, inference_duration=time.monotonic() - started
        )

    def push_frame(self, frame: rtc.AudioFrame) -> None:
        self._window.push_frame(frame)

    def flush(self) -> None:
        if len(self._window) > 0:
            self._window.shift(len(self._window))

    async def run(self) -> None:
        stream = self._stream() if self._stream is not None else None
        if stream is None:
            return
        await stream._drain_audio_channel()


class SmartTurnStream(_BaseStreamingTurnDetectorStream):
    def __init__(
        self, *, detector: SmartTurnDetector, opts: TurnDetectorOptions, predict: Predict
    ) -> None:
        super().__init__(detector=detector, opts=opts, transport=WindowTransport(predict))
        self._languages = detector.languages

    @property
    def model(self) -> str:  # type: ignore[override]
        return MODEL

    async def supports_language(self, language: LanguageCode | None) -> bool:
        return covers(language, self._languages)


class SmartTurnDetector(_BaseStreamingTurnDetector):
    def __init__(self, predict: Predict, languages: frozenset[str]) -> None:
        thresholds = ThresholdOptions(FRAMEWORK_THRESHOLDS_MODEL, COMPLETE_AT)
        super().__init__(opts=TurnDetectorOptions(sample_rate=SAMPLE_RATE, thresholds=thresholds))
        self._predict = predict
        self.languages = languages

    @property
    def model(self) -> str:  # type: ignore[override]
        return MODEL

    @property
    def provider(self) -> str:
        return PROVIDER

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> SmartTurnStream:
        return SmartTurnStream(detector=self, opts=self._opts, predict=self._predict)

    async def supports_language(self, language: LanguageCode | None) -> bool:
        return covers(language, self.languages)
