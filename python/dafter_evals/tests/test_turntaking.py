from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path

import numpy as np
import pytest
from dafter_evals.golden import GoldenClip, GoldenEntity
from dafter_evals.probe import Meter, Utterance, now
from dafter_evals.turntaking import (
    END,
    HOLD,
    TurnTrial,
    buckets_of,
    replay,
    summarize,
    turn_clips,
    turn_label,
)


def clip(clip_id: str, labels: tuple[str, ...], kinds: tuple[str, ...] = ()) -> GoldenClip:
    return GoldenClip(
        clip_id=clip_id,
        language="hi",
        audio_path=Path("audio/hi") / f"{clip_id}.wav",
        channel="telephony_8k",
        reference_native="x" * 10,
        reference_romanized=None,
        entities=tuple(GoldenEntity(kind, "x", 0, 1) for kind in kinds),
        labels=labels,
        consent_id="consent",
    )


class FakeProbe:
    def __init__(self, utterances: list[Utterance]) -> None:
        self.meter = Meter()
        self.states: list[tuple[float, str]] = []
        self._utterances = deque(utterances)

    async def settle(self) -> bool:
        return True

    def speak(self, pcm: np.ndarray) -> asyncio.Future[Utterance]:
        done: asyncio.Future[Utterance] = asyncio.get_running_loop().create_future()
        done.set_result(self._utterances.popleft())
        return done


def test_turn_labels_and_entity_buckets_come_from_the_clip() -> None:
    held = clip("a", ("turn:hold", "noise:traffic"), ("phone", "address"))
    assert turn_label(held) == HOLD
    assert buckets_of(held) == ("digit_string", "address")
    assert turn_label(clip("b", ("turn:end",))) == END
    assert turn_label(clip("c", ("backchannel:acknowledge",))) is None
    assert [c.clip_id for c in turn_clips([held, clip("c", ("codemix",))])] == ["a"]
    assert buckets_of(clip("d", ("turn:end",), ("name", "date"))) == ("spelled_name",)


def test_a_hold_is_cut_off_when_the_agent_takes_the_turn_inside_the_pause() -> None:
    quick = TurnTrial("a", HOLD, (), 250, 400)
    patient = TurnTrial("b", HOLD, (), 450, 600)
    never = TurnTrial("c", HOLD, (), None, None)
    early_end = TurnTrial("d", END, (), -100, 50)
    assert (quick.cut_off(300), quick.cut_off(600)) == (True, True)
    assert (patient.cut_off(300), patient.cut_off(600)) == (False, True)
    assert not never.cut_off(600)
    assert early_end.cut_off(300)
    assert not TurnTrial("e", END, (), 200, 300).cut_off(600)


def test_the_summary_splits_holds_ends_and_buckets() -> None:
    trials = [
        TurnTrial("a", HOLD, ("digit_string",), 250, 400),
        TurnTrial("b", HOLD, ("digit_string",), 450, 600),
        TurnTrial("c", HOLD, ("address",), None, None),
        TurnTrial("d", HOLD, (), 900, 1000),
        TurnTrial("e", END, (), 300, 500),
        TurnTrial("f", END, ("spelled_name",), 600, 900),
        TurnTrial("g", END, (), None, None),
    ]
    s = summarize(trials)
    assert s["hold"]["trials"] == 4
    assert s["hold"]["falseCutoffRate"] == {"300ms": 0.25, "600ms": 0.5}
    assert s["end"] == {
        "trials": 3,
        "answered": 2,
        "deadAirP50Ms": 700,
        "deadAirP95Ms": 880,
        "cutInsideClip": 0,
    }
    assert s["buckets"]["digit_string"]["falseCutoffRate"] == {"300ms": 0.5, "600ms": 1.0}
    assert s["buckets"]["address"]["falseCutoffRate"] == {"300ms": 0.0, "600ms": 0.0}
    assert s["buckets"]["spelled_name"]["trials"] == 1
    assert len(s["trialsDetail"]) == 7
    assert summarize([])["hold"]["falseCutoffRate"] == {"300ms": None, "600ms": None}


def test_replay_times_the_turn_from_the_end_of_the_clip() -> None:
    async def scenario() -> TurnTrial:
        base = now()
        probe = FakeProbe([Utterance(started=base, ended=base + 1.0)])
        probe.states.append((base + 1.15, "thinking"))
        probe.meter.add(base + 1.4, 0.2)
        held = clip("a", ("turn:hold",), ("phone",))
        return await replay(probe, held, np.zeros(10, dtype=np.int16))  # type: ignore[arg-type]

    trial = asyncio.run(scenario())
    assert (trial.take_ms, trial.dead_air_ms) == (150, 400)
    assert trial.buckets == ("digit_string",)


def test_replay_refuses_a_clip_without_a_turn_label() -> None:
    with pytest.raises(ValueError, match="no turn label"):
        asyncio.run(replay(FakeProbe([]), clip("a", ("codemix",)), np.zeros(1)))  # type: ignore[arg-type]
