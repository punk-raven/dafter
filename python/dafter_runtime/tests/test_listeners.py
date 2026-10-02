from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, cast

import pytest
from dafter_runtime.listeners import Listener, is_human, listener_options
from livekit import rtc
from livekit.agents import StopResponse, llm
from livekit.agents.types import ATTRIBUTE_PUBLISH_ON_BEHALF

AGENT = "agent-AJ_x"


@dataclass
class Someone:
    kind: int
    attributes: dict[str, str] = field(default_factory=dict)


@dataclass
class Local:
    identity: str = AGENT


@dataclass
class Room:
    local_participant: Local = field(default_factory=Local)


def human(p: Someone) -> bool:
    return is_human(cast(Any, p), cast(Any, Room()))


def test_every_human_kind_is_heard_and_agents_and_their_avatars_are_not() -> None:
    kinds = rtc.ParticipantKind
    assert human(Someone(kinds.PARTICIPANT_KIND_STANDARD))
    assert human(Someone(kinds.PARTICIPANT_KIND_SIP))
    assert not human(Someone(kinds.PARTICIPANT_KIND_AGENT))
    assert not human(Someone(kinds.PARTICIPANT_KIND_EGRESS))
    avatar = Someone(kinds.PARTICIPANT_KIND_STANDARD, {ATTRIBUTE_PUBLISH_ON_BEHALF: AGENT})
    assert not human(avatar)


def test_a_listener_hears_one_participant_and_never_speaks() -> None:
    options = listener_options("p_4b81e0d7", 16000)
    assert options.participant_identity == "p_4b81e0d7"
    audio = options.get_audio_input_options()
    assert audio is not None and audio.sample_rate == 16000
    assert options.get_audio_output_options() is None
    assert options.get_text_input_options() is None
    assert options.get_text_output_options() is not None
    assert options.close_on_disconnect is False


def test_a_listener_passes_each_finished_turn_and_its_timing_on_and_stops_the_reply() -> None:
    heard: list[tuple[str, str, llm.MetricsReport]] = []
    listener = Listener("p_4b81e0d7", lambda who, text, timing: heard.append((who, text, timing)))
    timing: llm.MetricsReport = {"end_of_turn_delay": 0.35, "transcription_delay": 0.12}

    async def turn(text: str) -> None:
        message = llm.ChatMessage(role="user", content=[text], metrics=timing)
        with pytest.raises(StopResponse):
            await listener.on_user_turn_completed(llm.ChatContext(), message)

    asyncio.run(turn(" निव्या, समय क्या है? "))
    asyncio.run(turn("  "))
    assert heard == [("p_4b81e0d7", "निव्या, समय क्या है?", timing)]
