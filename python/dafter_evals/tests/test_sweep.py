from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

import pytest
from dafter_evals.__main__ import (
    overrides,
    sweep,
    sweep_conditions,
    sweep_kinds,
    sweep_languages,
    without_detail,
)
from dafter_evals.scripts import LANGUAGES


def namespace(**given: Any) -> argparse.Namespace:
    defaults: dict[str, Any] = {
        "language": "hi",
        "sweep": "turntaking,vad",
        "conditions": "clean",
        "nc": "off",
        "sweep_languages": None,
        "golden_root": None,
        "noise_dir": None,
        "voices_dir": None,
        "seed": 7,
        "overrides": None,
        "echo": 0.0,
    }
    return argparse.Namespace(**{**defaults, **given})


def test_sweep_kinds_are_checked() -> None:
    assert sweep_kinds(namespace(sweep="vad, interruptions")) == {"vad", "interruptions"}
    with pytest.raises(SystemExit, match="--sweep takes"):
        sweep_kinds(namespace(sweep="vad,latency"))


def test_sweep_languages_default_to_the_language_and_take_all() -> None:
    assert sweep_languages(namespace(language="te-IN")) == ("te-IN",)
    assert sweep_languages(namespace(sweep_languages="all")) == LANGUAGES
    assert sweep_languages(namespace(sweep_languages="hi,mr-IN")) == ("hi", "mr-IN")
    with pytest.raises(SystemExit):
        sweep_languages(namespace(sweep_languages="hi,fr"))


def test_unknown_conditions_stop_the_run() -> None:
    assert [c.name for c in sweep_conditions(namespace(conditions="clean,quiet"))] == [
        "clean",
        "quiet",
    ]
    with pytest.raises(SystemExit, match="unknown condition"):
        sweep_conditions(namespace(conditions="reverb"))


def test_a_noise_filter_lands_in_the_agent_pipeline_override() -> None:
    assert "pipeline" not in overrides(namespace())["agent"]
    assert overrides(namespace(nc="nb"))["agent"]["pipeline"] == {"noiseFilter": "nc"}


def test_trial_detail_is_left_out_of_what_is_printed() -> None:
    report = {"a": {"trialsDetail": [1], "rate": 0.5}, "b": [{"trialsDetail": [], "n": 1}]}
    assert without_detail(report) == {"a": {"rate": 0.5}, "b": [{"n": 1}]}


def test_a_language_without_golden_clips_reports_zero_clips(tmp_path: Path) -> None:
    args = namespace(golden_root=tmp_path, sweep_languages="hi,kn-IN", conditions="clean,g711")
    report = asyncio.run(sweep(args))
    assert report["sweep"]["conditions"] == ["clean", "g711"]
    assert report["languages"] == {"hi": {"clips": 0}, "kn-IN": {"clips": 0}}


def test_the_snr_sweep_needs_noise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DAFTER_NOISE_DIR", raising=False)
    with pytest.raises(SystemExit, match="needs noise"):
        asyncio.run(sweep(namespace(golden_root=tmp_path, conditions="snr")))
