from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from dafter_core.config import parse
from dafter_core.enums import ErrorCode, Situation, SpeechNormalization
from dafter_core.errors import DafterError
from dafter_core.speech import every_phrase

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"

MINIMAL: dict[str, Any] = {
    "apiVersion": "dafter.dev/v1",
    "sessionId": "s_7f3a9c21",
    "tenantId": "t_9c21a4be",
    "privacyMode": "open",
    "language": "hi",
    "channel": "webrtc",
    "agent": {"enabled": True, "pool": "dafter-py"},
    "turn": {"strategy": "auto"},
    "recording": {"enabled": False},
    "budgets": {"turnGapP50Ms": 800, "turnGapP95Ms": 1500},
}


def doc(agent: dict[str, Any] | None = None, turn: dict[str, Any] | None = None) -> str:
    d = json.loads(json.dumps(MINIMAL))
    d["agent"].update(agent or {})
    d["turn"].update(turn or {})
    return json.dumps(d)


def refused_at(raw: str, pointer: str) -> None:
    with pytest.raises(DafterError) as exc:
        parse(raw)
    assert exc.value.code is ErrorCode.INVALID_CONFIG
    assert any(pointer in d for d in exc.value.details), exc.value.details


def test_a_document_without_the_blocks_keeps_the_old_behaviour_but_plans_its_text() -> None:
    c = parse(doc())
    assert c.agent.speech.normalization is SpeechNormalization.PLATFORM
    assert c.agent.speech.fillers.phrases == {}
    assert c.agent.speech.situations.cues == {}
    assert c.agent.speech.expressive is False
    assert c.turn.interruption.backchannel.words == {}


@pytest.mark.parametrize("mode", list(SpeechNormalization))
def test_every_normalization_and_cued_situation_is_accepted(mode: SpeechNormalization) -> None:
    cues = {"greeting": {"hi": ["नमस्ते"]}, "concern": {"hi": ["समस्या"]}}
    c = parse(doc(agent={"speech": {"normalization": str(mode), "situations": {"cues": cues}}}))
    assert c.agent.speech.normalization is mode
    assert set(c.agent.speech.situations.cues) == {Situation.GREETING, Situation.CONCERN}


def test_a_situation_or_normalization_nobody_implements_is_refused() -> None:
    refused_at(doc(agent={"speech": {"normalization": "llm"}}), "/agent/speech/normalization")
    angry = {"situations": {"cues": {"angry": {"hi": ["गुस्सा"]}}}}
    refused_at(doc(agent={"speech": angry}), "/agent/speech/situations/cues")


def test_the_backchannel_length_is_bounded() -> None:
    long = {"interruption": {"backchannel": {"maxWords": 7, "words": {"hi": ["हाँ"]}}}}
    refused_at(doc(turn=long), "/turn/interruption/backchannel/maxWords")


@pytest.mark.parametrize("ms", [99, 5001])
def test_the_backchannel_answer_window_is_bounded(ms: int) -> None:
    late = {"interruption": {"backchannel": {"answerWithinMs": ms, "words": {"hi": ["हाँ"]}}}}
    refused_at(doc(turn=late), "/turn/interruption/backchannel/answerWithinMs")


def test_the_catalog_states_every_language_it_hears_acknowledgements_and_fillers_in() -> None:
    c = parse(JOB.read_bytes())
    backchannel = c.turn.interruption.backchannel
    assert set(backchannel.words) == {"hi", "en", "kn", "mr", "te"}
    assert {"हाँ", "ok", "ಹೌದು", "हो", "అవును"} <= set(every_phrase(backchannel.words))
    assert backchannel.answer_within_ms == 1500
    assert set(c.agent.speech.fillers.phrases) == {"hi", "en", "kn", "mr", "te"}
    assert c.agent.speech.fillers.after_ms == 1000


MASCULINE = {"hi": ("देखता", "सकता", "रहा हूँ", "बताता"), "mr": ("बघतो", "शकतो", "बोलतोय")}


@pytest.mark.parametrize("language", sorted(MASCULINE))
def test_the_catalog_fillers_speak_of_the_agent_as_a_woman(language: str) -> None:
    phrases = parse(JOB.read_bytes()).agent.speech.fillers.phrases[language]
    assert phrases
    for phrase in phrases:
        assert not any(form in phrase for form in MASCULINE[language]), phrase
