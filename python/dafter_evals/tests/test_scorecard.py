from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from dafter_core.config import Budgets, parse
from dafter_evals.__main__ import route, usage
from dafter_evals.probe import Events
from dafter_evals.scorecard import (
    PENDING_ACCURACY,
    PENDING_LATENCY,
    PENDING_LIVE_ACCURACY,
    PENDING_NATURALNESS,
    main,
    markdown,
    scorecard,
)
from dafter_evals.turns import summarize

SARVAM = ("sarvam/saaras:v3-realtime", "sarvam/sarvam-105b", "sarvam/bulbul:v3")


def accuracy(language: str, mode: str, hearing: str, wer: float, identified: int | None) -> Any:
    return {
        "kind": "dafter.asr.accuracy",
        "ranAt": "2026-09-28T10:00:00+00:00",
        "dataset": "kathbath",
        "language": language,
        "stt": {
            "provider": "sarvam",
            "model": "saaras:v3-realtime",
            "mode": mode,
            "hearing": hearing,
        },
        "summary": {
            "clips": 20,
            "failed": 0,
            "referenceWords": 200,
            "wer": wer,
            "latinWords": 7,
            "identifiedCorrectly": identified,
            "identifiedOf": 20 if identified is not None else None,
            "finalAfterAudioMs": {"p50": 400, "p95": 700},
        },
    }


def live(language: str, gap_p50: int, cost: float | None) -> Any:
    report: dict[str, Any] = summarize([], [], Events(), Budgets(800, 1500))
    report["summary"]["caller"]["gap_ms"].update(p50=gap_p50, p95=gap_p50 + 300)
    report["session"] = {
        "sessionId": "s_7f3a9c21",
        "configHash": "0" * 64,
        "language": language,
        "pipeline": dict(zip(("stt", "llm", "tts"), SARVAM, strict=True)),
    }
    report["usage"] = {
        "callSeconds": 120.0,
        "costInr": cost,
        "costPerMinuteInr": cost,
        "unpriced": [],
    }
    return report


def test_each_language_gets_a_card_per_stack_with_what_is_still_pending() -> None:
    reports = [
        accuracy("kn-IN", "transcribe", "kn-IN", 0.21, None),
        accuracy("kn-IN", "codemix", "identify", 0.25, 19),
        accuracy("hi", "codemix", "hi", 0.12, None),
    ]
    ratings = {"hi": {"sarvam/bulbul:v3": {"score": 4.2, "raters": 3, "clips": 10}}}
    card = scorecard(reports, [live("hi", 1100, 1.25)], ratings, ["hi", "kn-IN", "te-IN"])
    languages = card["languages"]
    assert languages["te-IN"]["stacks"] == []
    (kn,) = languages["kn-IN"]["stacks"]
    assert (kn["stt"], kn["llm"], kn["tts"]) == (SARVAM[0], None, None)
    assert [r["wer"] for r in kn["errorRate"]["offline"]] == [0.21, 0.25]
    assert kn["errorRate"]["offline"][1]["identified"] == 0.95
    assert kn["latency"] is None
    assert kn["costPerMinuteInr"] == {"sttPerAudioMinute": 0.5, "perCallMinute": None}
    assert PENDING_LATENCY in kn["pending"] and PENDING_NATURALNESS in kn["pending"]
    (hi,) = languages["hi"]["stacks"]
    assert (hi["stt"], hi["llm"], hi["tts"]) == SARVAM
    assert hi["latency"][0]["caller"]["gap_ms"] == {"p50": 1100, "p95": 1400}
    assert hi["costPerMinuteInr"]["perCallMinute"] == 1.25
    assert hi["naturalness"]["score"] == 4.2
    assert hi["pending"] == [PENDING_LIVE_ACCURACY]


def test_a_live_run_without_an_offline_run_is_pending_its_error_rate() -> None:
    card = scorecard([], [live("en-IN", 900, None)], {}, ["en-IN"])
    (en,) = card["languages"]["en-IN"]["stacks"]
    assert PENDING_ACCURACY in en["pending"]
    assert en["costPerMinuteInr"]["perCallMinute"] is None


def test_the_table_names_every_language_and_marks_what_is_pending(tmp_path: Path) -> None:
    kn = tmp_path / "kn.json"
    kn.write_text(json.dumps(accuracy("kn-IN", "codemix", "identify", 0.25, 19)))
    out, table = tmp_path / "card.json", tmp_path / "card.md"
    assert main(["--accuracy", str(kn), "--out", str(out), "--markdown", str(table)]) == 0
    text = table.read_text(encoding="utf-8")
    assert "## kn-IN" in text and "## mr-IN" in text
    assert "kathbath codemix identify: 25.0%, 95% identified" in text
    assert "pending: no run for this language" in text
    assert json.loads(out.read_text())["kind"] == "dafter.scorecard"
    assert markdown(json.loads(out.read_text())) == text.rstrip("\n")


def test_a_live_run_records_its_route_and_what_a_minute_of_call_cost() -> None:
    job = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "telugu-webrtc-job.json"
    assert route(parse(job.read_bytes())) == dict(zip(("stt", "llm", "tts"), SARVAM, strict=True))
    item = {"stage": "llm", "provider": "sarvam", "model": "sarvam-105b", "priced": False}
    spent = usage({"costInr": 2.5, "items": [item]}, 120.0)
    assert spent == {
        "callSeconds": 120.0,
        "costInr": 2.5,
        "costPerMinuteInr": 1.25,
        "unpriced": ["llm:sarvam/sarvam-105b"],
    }
    assert usage(None, 60.0)["costPerMinuteInr"] is None
