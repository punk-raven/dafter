from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dafter_core.enums import EventType
from dafter_core.events import parse_event
from dafter_runtime.events import SessionEvents
from dafter_runtime.metrics import WorkerMetrics
from dafter_runtime.plan import load, plan
from dafter_runtime.worker import watch
from livekit import rtc
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    Agent,
    AgentSession,
    APIConnectOptions,
    llm,
    tts,
)
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.voice import io
from livekit.agents.voice.transcription.synchronizer import TranscriptSynchronizer
from opentelemetry import trace
from prometheus_client import CollectorRegistry

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
RATE = 16000
TICK = 0.01
REPLY = "नमस्ते! बताइए, मैं आपकी क्या मदद कर सकती हूँ? आज मौसम बहुत अच्छा है।"


def silence(seconds: float) -> rtc.AudioFrame:
    samples = int(RATE * seconds)
    return rtc.AudioFrame(b"\0\0" * samples, RATE, 1, samples)


class Speaker(io.AudioOutput):
    def __init__(self) -> None:
        super().__init__(label="speaker", capabilities=io.AudioOutputCapabilities(pause=False))
        self.queued = 0.0
        self._playing: asyncio.Task[None] | None = None

    async def capture_frame(self, frame: rtc.AudioFrame) -> None:
        await super().capture_frame(frame)
        if self.queued == 0.0:
            self.on_playback_started(created_at=time.time())
        self.queued += frame.duration

    def flush(self) -> None:
        super().flush()
        self._playing = asyncio.ensure_future(self._play(self.queued))

    async def _play(self, duration: float) -> None:
        await asyncio.sleep(duration)
        self.on_playback_finished(playback_position=duration, interrupted=False)

    def clear_buffer(self) -> None:
        if self._playing is not None and not self._playing.done():
            self._playing.cancel()
            self.on_playback_finished(playback_position=0.0, interrupted=True)


class Captions(io.TextOutput):
    def __init__(self) -> None:
        super().__init__(label="captions", next_in_chain=None)

    async def capture_text(self, text: str) -> None:
        return None

    def flush(self) -> None:
        return None


class Reading(tts.ChunkedStream):
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id="read", sample_rate=RATE, num_channels=1, mime_type="audio/pcm"
        )
        output_emitter.push(silence(3.0).data.tobytes())


class Reader(tts.TTS[Any]):
    def __init__(self) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False), sample_rate=RATE, num_channels=1
        )

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> Reading:
        return Reading(tts=self, input_text=text, conn_options=conn_options)


class Answer(llm.LLMStream):
    async def _run(self) -> None:
        delta = llm.ChoiceDelta(role="assistant", content=REPLY)
        self._event_ch.send_nowait(llm.ChatChunk(id="answer", delta=delta))


class Answerer(llm.LLM[Any]):
    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> llm.LLMStream:
        return Answer(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


def rows_when_closed(after_playback_s: float) -> list[dict[str, Any]]:
    p = plan(load(JOB.read_bytes().strip()), "dafter-py")
    sent: list[bytes] = []

    async def publish(body: bytes) -> None:
        sent.append(body)

    async def run() -> None:
        events = SessionEvents(p.config, publish, clock=lambda: datetime(2026, 9, 28, tzinfo=UTC))
        session = AgentSession[None](llm=Answerer(), tts=Reader())
        watch(session, p, events, trace.NoOpTracer(), WorkerMetrics(CollectorRegistry()))
        speaker = Speaker()
        synchronizer = TranscriptSynchronizer(
            next_in_chain_audio=speaker, next_in_chain_text=Captions()
        )
        session.output.audio = synchronizer.audio_output
        session.output.transcription = synchronizer.text_output
        await session.start(Agent(instructions=p.persona.instructions))
        session.generate_reply(user_input="नमस्ते")
        deadline = time.monotonic() + 5
        while speaker.queued == 0.0 and time.monotonic() < deadline:
            await asyncio.sleep(TICK)
        assert speaker.queued > 0.0
        await asyncio.sleep(after_playback_s)
        await session.aclose()
        await events.drain()

    asyncio.run(run())
    parsed = [parse_event(body) for body in sent]
    return [e.payload for e in parsed if e.type is EventType.AGENT_TURN_METRICS]


def test_a_reply_the_session_closed_on_before_its_first_word_still_gets_a_row() -> None:
    assert rows_when_closed(0.05) == [
        {"turn": 0, "interrupted": True, "configVersion": {"id": "nivya-v1", "arm": "stable"}}
    ]


def test_a_reply_cut_off_after_its_first_words_gets_one_row_with_its_layers() -> None:
    [row] = rows_when_closed(1.5)
    assert row["interrupted"] is True
    assert "llmNodeTtftMs" in row and "ttsNodeTtfbMs" in row
