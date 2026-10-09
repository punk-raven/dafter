from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, cast

import pytest
from dafter_core.hashing import seal
from dafter_runtime.answering import Roster
from dafter_runtime.backchannel import Acknowledgements, Reply
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.toolbox import Answering, registry_for
from livekit.agents import Agent, LanguageCode, ModelSettings, stt

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-telephony-job.json"
CATALOG = Path(__file__).resolve().parents[3] / "go" / "cmd" / "dafter-control" / "catalog.json"


class SpeakingFloor:
    def __init__(self) -> None:
        self.cuts = 0

    def held(self) -> bool:
        return True

    def cut(self) -> None:
        self.cuts += 1

    def heard(self) -> Reply | None:
        return None


def phone_plan() -> Plan:
    doc = json.loads(JOB.read_bytes())
    doc["agent"]["addressing"]["mode"] = "always"
    defaults = json.loads(CATALOG.read_text(encoding="utf-8"))["defaults"]
    doc["turn"]["interruption"]["backchannel"] = defaults["turn"]["interruption"]["backchannel"]
    doc["turn"]["interruption"]["backchannel"]["enabled"] = True
    sealed, _ = seal(json.dumps(doc))
    return plan(load(sealed), "dafter-py")


def spoken(*texts: str) -> list[stt.SpeechEvent]:
    def transcript(kind: stt.SpeechEventType, text: str) -> stt.SpeechEvent:
        heard = stt.SpeechData(language=LanguageCode("hi"), text=text)
        return stt.SpeechEvent(type=kind, alternatives=[heard])

    return [
        stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH),
        *(transcript(stt.SpeechEventType.INTERIM_TRANSCRIPT, t) for t in texts[:-1]),
        transcript(stt.SpeechEventType.FINAL_TRANSCRIPT, texts[-1]),
        stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH),
    ]


def cuts_over_the_agent(monkeypatch: pytest.MonkeyPatch, *texts: str) -> int:
    async def recognized(agent: Agent, audio: object, settings: object) -> AsyncIterator[object]:
        for event in spoken(*texts):
            yield event

    monkeypatch.setattr(Agent.default, "stt_node", recognized)
    p = phone_plan()
    heard = Acknowledgements.of(p.config.turn.interruption.backchannel)
    assert heard is not None

    async def hear() -> int:
        registry = registry_for(p, cast(Any, None), Roster(), lambda: None, None)
        answering = Answering(p.persona.instructions, registry, lambda: None, heard)
        floor = SpeakingFloor()
        answering._floor = floor
        async for _ in answering.stt_node(cast(Any, None), ModelSettings()):
            pass
        return floor.cuts

    return asyncio.run(hear())


@pytest.mark.parametrize("texts", [("no", "no no"), ("ruko",), ("रुको",), ("नहीं",)])
def test_a_short_negative_on_a_phone_line_cuts_the_agent(
    monkeypatch: pytest.MonkeyPatch, texts: tuple[str, ...]
) -> None:
    assert cuts_over_the_agent(monkeypatch, *texts) >= 1


def test_an_acknowledgement_on_a_phone_line_does_not_cut_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert cuts_over_the_agent(monkeypatch, "हाँ", "हाँ जी") == 0
