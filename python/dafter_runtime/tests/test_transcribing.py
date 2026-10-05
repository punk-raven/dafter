from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from dafter_core.hashing import seal
from dafter_runtime.captions import Captions
from dafter_runtime.events import SessionEvents
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.stages import build
from dafter_runtime.transcribing import Transcribing
from livekit import rtc

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
ASHA = "p_4b81e0d7"
RAVI = "p_9d02c3aa"


@pytest.fixture(autouse=True)
def sarvam_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARVAM_API_KEY", "test-only-not-a-key")


def live_plan() -> Plan:
    doc = json.loads(JOB.read_bytes())
    doc["agent"]["pipeline"]["tts"]["options"]["prewarm"] = False
    doc["agent"]["pipeline"]["llm"]["options"]["prewarm"] = False
    doc["transcription"] = {"mode": "live", "consentArtifactId": "consent_tr"}
    sealed, _ = seal(json.dumps(doc))
    return plan(load(sealed), "dafter-py")


@dataclass
class Someone:
    identity: str
    kind: int = rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class Local:
    identity: str = "agent-AJ_x"


@dataclass
class Room:
    remote_participants: dict[str, Someone] = field(default_factory=dict)
    local_participant: Local = field(default_factory=Local)
    handlers: dict[str, Callable[[Someone], None]] = field(default_factory=dict)

    def on(self, event: str, callback: Callable[[Someone], None]) -> None:
        self.handlers[event] = callback


@dataclass
class Ctx:
    room: Room
    shutdown: list[Any] = field(default_factory=list)

    def add_shutdown_callback(self, callback: Any) -> None:
        self.shutdown.append(callback)


class Recorded:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def join(self, identity: str) -> None:
        self.calls.append(("join", identity))

    async def leave(self, identity: str) -> None:
        self.calls.append(("leave", identity))

    async def aclose(self) -> None:
        self.calls.append(("close", ""))


def test_everyone_human_is_transcribed_while_they_are_in_the_call() -> None:
    async def run() -> tuple[list[tuple[str, str]], Ctx]:
        p = live_plan()
        stages = build(p)

        async def publish(body: bytes) -> None:
            return None

        events = SessionEvents(p.config, publish, clock=lambda: datetime(2026, 9, 24, tzinfo=UTC))
        room = Room({ASHA: Someone(ASHA)})
        ctx = Ctx(room)
        transcribing = Transcribing(cast(Any, ctx), p, stages, Captions(events.emit, None), 16000)
        recorded = Recorded()
        transcribing.listeners = cast(Any, recorded)
        transcribing.listen()
        room.handlers["participant_connected"](Someone(RAVI))
        room.handlers["participant_connected"](
            Someone("agent-AJ_y", rtc.ParticipantKind.PARTICIPANT_KIND_AGENT)
        )
        room.handlers["participant_disconnected"](Someone(ASHA))
        await asyncio.sleep(0)
        await stages.stt.aclose()
        await stages.tts.aclose()
        await stages.llm.aclose()
        return recorded.calls, ctx

    calls, ctx = asyncio.run(run())
    assert calls == [("join", ASHA), ("join", RAVI), ("leave", ASHA)]
    assert len(ctx.shutdown) == 1
