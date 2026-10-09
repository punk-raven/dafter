from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from dafter_evals import __main__ as cli
from dafter_evals import sweepgate
from dafter_evals.scripts import Caller

THRESHOLDS = {
    "thresholds": {
        "hi": {
            "clean": {
                "interruptions.tStopP50Ms": {"max": 300},
                "vad.rocAuc": {"min": 0.9},
            }
        }
    }
}


def swept(stop_ms: int | None, roc_auc: float = 0.95) -> dict[str, Any]:
    row = {"interruptions": {"tStopP50Ms": stop_ms}, "vad": {"rocAuc": roc_auc}}
    return {"sweep": {}, "languages": {"hi": {"clips": 4, "conditions": {"clean": row}}}}


def scripted(go: bool) -> dict[str, Any]:
    return {"summary": {"verdict": {"go": go, "checks": {}}}}


def exit_code(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(sys, "argv", ["dafter-evals", *argv])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert isinstance(exited.value.code, int)
    return exited.value.code


def stub_script(monkeypatch: pytest.MonkeyPatch, go: bool) -> None:
    async def evaluate(args: argparse.Namespace, caller: Caller) -> dict[str, Any]:
        return scripted(go)

    monkeypatch.setattr(cli, "evaluate", evaluate)


def stub_sweep(monkeypatch: pytest.MonkeyPatch, report: dict[str, Any]) -> None:
    async def sweep(args: argparse.Namespace) -> dict[str, Any]:
        return report

    monkeypatch.setattr(cli, "sweep", sweep)


def baseline(tmp_path: Path, content: object = THRESHOLDS) -> str:
    path = tmp_path / "sweep-baseline.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize(("go", "argv", "code"), [(True, [], 0), (False, [], 1)])
def test_a_scripted_run_exits_on_its_budget_verdict(
    go: bool, argv: list[str], code: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_script(monkeypatch, go)
    assert exit_code(argv, monkeypatch) == code


def test_report_only_keeps_a_failed_scripted_run_at_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_script(monkeypatch, go=False)
    assert exit_code(["--report-only"], monkeypatch) == 0


def test_gate_on_a_scripted_run_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_script(monkeypatch, go=True)
    assert exit_code(["--gate"], monkeypatch) == 2


def test_a_sweep_without_gate_only_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    stub_sweep(monkeypatch, swept(stop_ms=900))
    assert exit_code(["--sweep", "interruptions"], monkeypatch) == 0


def test_a_gated_sweep_exits_on_its_baseline_thresholds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--sweep", "interruptions,vad", "--gate", "--baseline", baseline(tmp_path)]
    stub_sweep(monkeypatch, swept(stop_ms=250))
    assert exit_code(argv, monkeypatch) == 0
    stub_sweep(monkeypatch, swept(stop_ms=900))
    capsys.readouterr()
    assert exit_code(argv, monkeypatch) == 1
    shown = json.loads(capsys.readouterr().out)
    assert [c["pass"] for c in shown["gate"]["checks"]] == [False, True]
    assert exit_code([*argv, "--report-only"], monkeypatch) == 0


def test_a_gated_sweep_needs_readable_thresholds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_sweep(monkeypatch, swept(stop_ms=250))
    assert exit_code(["--sweep", "vad", "--gate"], monkeypatch) == 2
    bad = baseline(tmp_path, {"thresholds": {"hi": {"clean": {"vad.rocAuc": {"above": 1}}}}})
    assert exit_code(["--sweep", "vad", "--gate", "--baseline", bad], monkeypatch) == 2


def test_an_unmeasured_metric_fails_and_an_unswept_condition_is_named() -> None:
    verdict = sweepgate.judge(swept(stop_ms=None), sweepgate.parse(THRESHOLDS))
    assert verdict["go"] is False and verdict["checks"][0]["measured"] is None
    extra = sweepgate.parse({"thresholds": {"kn-IN": {"clean": {"vad.rocAuc": {"min": 0.9}}}}})
    nothing = sweepgate.judge(swept(stop_ms=250), extra)
    assert nothing == {"go": False, "checks": [], "notRun": ["kn-IN/clean"]}


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"thresholds": {}},
        {"thresholds": {"hi": {}}},
        {"thresholds": {"hi": {"clean": {}}}},
        {"thresholds": {"hi": {"clean": {"vad.rocAuc": {}}}}},
        {"thresholds": {"hi": {"clean": {"vad.rocAuc": {"min": True}}}}},
    ],
)
def test_malformed_thresholds_are_refused(raw: object) -> None:
    with pytest.raises(ValueError):
        sweepgate.parse(raw)
