from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dafter_core.enums import EventType
from dafter_core.events import EventEnvelope, parse_event
from dafter_core.hashing import seal
from dafter_runtime.captions import AgentCaptions, Captions, segment
from dafter_runtime.events import SessionEvents
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.worker import captions_for, room_options
from livekit.agents import AgentSession
from livekit.agents.language import LanguageCode
from livekit.agents.voice.events import UserInputTranscribedEvent

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
ASHA = "p_4b81e0d7"
RAVI = "p_9d02c3aa"
SARVAM = {"provider": "sarvam", "model": "saaras:v3-realtime"}


def live_plan(addressing: str = "always") -> Plan:
    doc = json.loads(JOB.read_bytes())
    doc["agent"]["addressing"]["mode"] = addressing
    doc["transcription"] = {"mode": "live", "consentArtifactId": "consent_tr"}
    sealed, _ = seal(json.dumps(doc))
    return plan(load(sealed), "dafter-py")


class Sent:
    def __init__(self) -> None:
        self.bodies: list[bytes] = []
        cfg = load(JOB.read_bytes().strip())

        async def publish(body: bytes) -> None:
            self.bodies.append(body)

        self.events = SessionEvents(
            cfg, publish, clock=lambda: datetime(2026, 9, 24, 10, 0, tzinfo=UTC)
        )

    def parsed(self) -> list[EventEnvelope]:
        return [parse_event(b) for b in self.bodies]


def captured(script: Any) -> list[EventEnvelope]:
    sent = Sent()

    async def run() -> None:
        await script(sent.events)
        await sent.events.drain()

    asyncio.run(run())
    return sent.parsed()


def test_a_segment_is_partial_until_it_settles_and_then_a_new_one_opens() -> None:
    async def script(events: SessionEvents) -> None:
        captions = Captions(events.emit, SARVAM)
        captions.heard(ASHA, "मुझे कल", False, "hi-IN")
        captions.heard(ASHA, "मुझे कल की मीटिंग", False, "hi-IN")
        captions.heard(RAVI, "हाँ", True, "hi-IN")
        captions.heard(ASHA, "मुझे कल की मीटिंग का टाइम बताओ", True, "hi-IN")
        captions.heard(ASHA, "और", False, "hi-IN")

    sent = captured(script)
    assert [e.type for e in sent] == [
        EventType.TRANSCRIPT_PARTIAL,
        EventType.TRANSCRIPT_PARTIAL,
        EventType.TRANSCRIPT_FINAL,
        EventType.TRANSCRIPT_FINAL,
        EventType.TRANSCRIPT_PARTIAL,
    ]
    ids = [e.payload["segmentId"] for e in sent]
    assert ids[0] == ids[1] == ids[3]
    assert len({ids[0], ids[2], ids[4]}) == 3
    assert sent[3].payload == {
        "segmentId": ids[0],
        "speaker": {"kind": "human", "participantId": ASHA},
        "text": "मुझे कल की मीटिंग का टाइम बताओ",
        "language": "hi-IN",
        "source": SARVAM,
    }
    assert sent[2].payload["speaker"] == {"kind": "human", "participantId": RAVI}


def test_silence_is_not_a_caption_and_a_language_outside_bcp47_is_left_out() -> None:
    async def script(events: SessionEvents) -> None:
        captions = Captions(events.emit, None)
        captions.heard(ASHA, "  ", False, "hi-IN")
        captions.heard(ASHA, ".", True, "hi-IN")
        captions.heard(ASHA, "\u200b", True, "hi-IN")
        captions.heard(ASHA, "hello", True, "multi-lingual")

    [only] = captured(script)
    assert only.payload["text"] == "hello"
    assert "language" not in only.payload
    assert "source" not in only.payload


def test_the_agent_is_captioned_as_it_speaks_and_names_no_participant() -> None:
    async def script(events: SessionEvents) -> None:
        agent = AgentCaptions(events.emit)
        agent.flush()
        for word in ("जी, ", "कल की ", "मीटिंग ", "दस बजे है।"):
            await agent.capture_text(word)
        agent.flush()
        await agent.capture_text("और कुछ?")
        agent.flush()

    sent = captured(script)
    assert [e.type for e in sent] == [EventType.TRANSCRIPT_PARTIAL] * 4 + [
        EventType.TRANSCRIPT_FINAL,
        EventType.TRANSCRIPT_PARTIAL,
        EventType.TRANSCRIPT_FINAL,
    ]
    assert sent[4].payload["text"] == "जी, कल की मीटिंग दस बजे है।"
    assert sent[4].payload["speaker"] == {"kind": "agent"}
    assert "source" not in sent[4].payload
    assert sent[0].payload["segmentId"] == sent[4].payload["segmentId"]
    assert sent[5].payload["segmentId"] != sent[4].payload["segmentId"]


def test_a_listener_session_feeds_its_participants_captions() -> None:
    async def script(events: SessionEvents) -> None:
        captions = Captions(events.emit, SARVAM)
        listener: AgentSession[None] = AgentSession()
        captions.follow(ASHA, listener)
        listener.emit(
            "user_input_transcribed",
            UserInputTranscribedEvent(
                transcript="रुको", is_final=False, language=LanguageCode("hi-IN")
            ),
        )
        listener.emit(
            "user_input_transcribed",
            UserInputTranscribedEvent(
                transcript="रुको ज़रा", is_final=True, language=LanguageCode("hi-IN")
            ),
        )
        stranger: AgentSession[None] = AgentSession()
        captions.follow("loadtest-7", stranger)
        stranger.emit(
            "user_input_transcribed", UserInputTranscribedEvent(transcript="hi", is_final=True)
        )
        await listener.aclose()
        await stranger.aclose()

    sent = captured(script)
    assert [(e.type, e.payload["text"]) for e in sent] == [
        (EventType.TRANSCRIPT_PARTIAL, "रुको"),
        (EventType.TRANSCRIPT_FINAL, "रुको ज़रा"),
    ]


def test_a_participant_who_leaves_mid_segment_starts_fresh_on_return() -> None:
    async def script(events: SessionEvents) -> None:
        captions = Captions(events.emit, SARVAM)
        captions.heard(ASHA, "मुझे", False, None)
        captions.left(ASHA)
        captions.heard(ASHA, "नमस्ते", False, None)

    first, second = captured(script)
    assert first.payload["segmentId"] != second.payload["segmentId"]


def test_captions_run_only_when_transcription_is_live() -> None:
    sent = Sent()
    assert captions_for(plan(load(JOB.read_bytes().strip()), "dafter-py"), sent.events) is None
    for addressing in ("always", "transcript"):
        p = live_plan(addressing)
        captions = captions_for(p, sent.events)
        assert captions is not None
        text = room_options(p, 24000, captions).get_text_output_options()
        assert text is not None and text.next_in_chain is captions.agent


def test_human_captions_name_the_session_recognizer() -> None:
    async def script(events: SessionEvents) -> None:
        captions = captions_for(live_plan(), events)
        assert captions is not None
        captions.heard(ASHA, "नमस्ते", True, "hi-IN")

    [only] = captured(script)
    assert only.payload["source"] == SARVAM


def test_a_caption_is_capped_at_the_schema_length() -> None:
    payload = segment("sg_0123456789abcdef", {"kind": "agent"}, "x" * 5000)
    assert len(payload["text"]) == 4000
