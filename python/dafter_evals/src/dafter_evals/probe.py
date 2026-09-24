from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field

import numpy as np
from dafter_core.errors import DafterError
from dafter_core.events import parse_event
from livekit import rtc

EVENTS_TOPIC = "dafter.events"
SAMPLE_RATE = 24000
FRAME_MS = 10
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000
LOUD = 0.02


def now() -> float:
    return time.perf_counter()


def rms(samples: np.ndarray) -> float:
    if samples.size == 0:
        return 0.0
    x = samples.astype(np.float32) / 32768.0
    return float(np.sqrt(np.mean(x * x)))


def trim(pcm: np.ndarray, threshold: float = LOUD) -> np.ndarray:
    frames = [pcm[i : i + SAMPLES_PER_FRAME] for i in range(0, pcm.size, SAMPLES_PER_FRAME)]
    loud = [i for i, f in enumerate(frames) if rms(f) >= threshold]
    if not loud:
        return pcm[:0]
    return pcm[loud[0] * SAMPLES_PER_FRAME : (loud[-1] + 1) * SAMPLES_PER_FRAME]


@dataclass
class Meter:
    frames: list[tuple[float, float]] = field(default_factory=list)

    def add(self, t: float, level: float) -> None:
        self.frames.append((t, level))

    def first_loud_after(self, t: float) -> float | None:
        return next((ft for ft, lv in self.frames if ft > t and lv >= LOUD), None)

    def last_loud_before(self, t: float) -> float | None:
        loud = [ft for ft, lv in self.frames if ft <= t and lv >= LOUD]
        return loud[-1] if loud else None

    def quiet_run_start(self, after: float, until: float, run: float) -> float | None:
        last_loud = after
        for ft, lv in self.frames:
            if ft <= after:
                continue
            if ft > until:
                break
            if lv >= LOUD:
                last_loud = ft
            elif ft - last_loud >= run:
                return last_loud
        return None

    def loud_since(self, t: float) -> bool:
        return any(ft > t and lv >= LOUD for ft, lv in self.frames)


@dataclass
class Utterance:
    started: float
    ended: float


class Probe:
    def __init__(self) -> None:
        self.room = rtc.Room()
        self.source = rtc.AudioSource(SAMPLE_RATE, 1, queue_size_ms=40)
        self.meter = Meter()
        self.states: list[tuple[float, str]] = []
        self.event_errors = 0
        self._queue: deque[tuple[np.ndarray, asyncio.Future[Utterance]]] = deque()
        self._agent_audio = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    @property
    def state(self) -> str | None:
        return self.states[-1][1] if self.states else None

    async def connect(self, url: str, token: str) -> None:
        self.room.on("track_subscribed", self._subscribed)
        self.room.on("data_received", self._data)
        await self.room.connect(url, token)
        track = rtc.LocalAudioTrack.create_audio_track("microphone", self.source)
        options = rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)
        await self.room.local_participant.publish_track(track, options)
        self._tasks.append(asyncio.create_task(self._pump()))

    async def close(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.room.disconnect()

    async def wait_agent_audio(self, timeout: float) -> None:
        await asyncio.wait_for(self._agent_audio.wait(), timeout)

    def speak(self, pcm: np.ndarray) -> asyncio.Future[Utterance]:
        done: asyncio.Future[Utterance] = asyncio.get_running_loop().create_future()
        self._queue.append((pcm, done))
        return done

    async def wait_state(self, state: str, after: float, timeout: float) -> float | None:
        deadline = now() + timeout
        while now() < deadline:
            hit = next((t for t, s in self.states if t > after and s == state), None)
            if hit is not None:
                return hit
            await asyncio.sleep(0.01)
        return None

    async def settle(self, quiet: float = 0.7, timeout: float = 30.0) -> bool:
        deadline = now() + timeout
        while now() < deadline:
            last = self.meter.last_loud_before(now())
            if self.state == "listening" and (last is None or now() - last >= quiet):
                return True
            await asyncio.sleep(0.05)
        return False

    async def _pump(self) -> None:
        silence = np.zeros(SAMPLES_PER_FRAME, dtype=np.int16)
        while True:
            if not self._queue:
                await self.source.capture_frame(self._frame(silence))
                continue
            pcm, done = self._queue.popleft()
            started: float | None = None
            for i in range(0, pcm.size, SAMPLES_PER_FRAME):
                chunk = pcm[i : i + SAMPLES_PER_FRAME]
                if chunk.size < SAMPLES_PER_FRAME:
                    chunk = np.pad(chunk, (0, SAMPLES_PER_FRAME - chunk.size))
                await self.source.capture_frame(self._frame(chunk))
                if started is None:
                    started = now() + self.source.queued_duration
            ended = now() + self.source.queued_duration
            if not done.done():
                done.set_result(Utterance(started=started or ended, ended=ended))

    def _frame(self, samples: np.ndarray) -> rtc.AudioFrame:
        return rtc.AudioFrame(samples.tobytes(), SAMPLE_RATE, 1, samples.size)

    def _subscribed(
        self,
        track: rtc.Track,
        publication: rtc.RemoteTrackPublication,
        participant: rtc.RemoteParticipant,
    ) -> None:
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            self._tasks.append(asyncio.create_task(self._listen(track)))

    async def _listen(self, track: rtc.Track) -> None:
        stream = rtc.AudioStream(track, sample_rate=SAMPLE_RATE, num_channels=1)
        async for event in stream:
            samples = np.frombuffer(event.frame.data, dtype=np.int16)
            level = rms(samples)
            self.meter.add(now(), level)
            if level >= LOUD:
                self._agent_audio.set()

    def _data(self, packet: rtc.DataPacket) -> None:
        if packet.topic != EVENTS_TOPIC:
            return
        try:
            event = parse_event(bytes(packet.data))
        except DafterError:
            self.event_errors += 1
            return
        self.states.append((now(), str(event.payload.get("state"))))
