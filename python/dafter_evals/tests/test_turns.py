from __future__ import annotations

import numpy as np
from dafter_core.config import Budgets
from dafter_evals.probe import SAMPLES_PER_FRAME, Events, Meter, trim
from dafter_evals.turns import OverlapResult, TurnResult, summarize

BUDGETS = Budgets(turn_gap_p50_ms=800, turn_gap_p95_ms=1500, barge_in_stop_p50_ms=300)


def test_trim_cuts_the_silence_the_voice_leaves_around_speech() -> None:
    quiet = np.zeros(SAMPLES_PER_FRAME * 5, dtype=np.int16)
    loud = np.full(SAMPLES_PER_FRAME * 3, 8000, dtype=np.int16)
    trimmed = trim(np.concatenate([quiet, loud, quiet]))
    assert trimmed.size == loud.size
    assert trim(quiet).size == 0


def test_the_meter_finds_where_the_agent_went_quiet() -> None:
    m = Meter()
    for i in range(100):
        m.add(i * 0.01, 0.1 if i < 40 else 0.0)
    assert m.first_loud_after(-1) == 0.0
    assert m.last_loud_before(0.8) == 0.39
    assert m.quiet_run_start(0.1, 1.0, 0.3) == 0.39
    assert not m.loud_since(0.5)


def test_the_summary_counts_only_answered_turns() -> None:
    turns = [
        TurnResult(0, 900, 700, 300, 400),
        TurnResult(1, 900, None, None, None),
        TurnResult(2, 900, 900, 350, 550),
    ]
    overlaps = [
        OverlapResult("barge_in", "रुकिए", 1000, True, 250, ["listening"], False, False, 180)
    ]
    s = summarize(turns, overlaps, Events(), BUDGETS)["summary"]
    assert (s["turns"], s["answered"], s["caller"]["gap_ms"]["p50"]) == (3, 2, 800)
    assert s["caller"]["end_of_turn_ms"] == {"n": 2, "p50": 325, "p95": 348}
    assert s["barge_in"] == {
        "clock": "caller",
        "trials": 1,
        "stopped": 1,
        "inconclusive": 0,
        "replied_to": 0,
        "stop_p50_ms": 250,
        "stop_max_ms": 250,
        "state_stopped": 1,
        "state_stop_p50_ms": 180,
        "state_stop_p95_ms": 180,
    }
