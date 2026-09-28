from __future__ import annotations

from pathlib import Path

import pytest
from dafter_providers.openai_compat.client import CompatLLM
from dafter_providers.sarvam.llm import SarvamLLM
from dafter_runtime.plan import load
from dafter_runtime.plan import plan as agent_plan
from dafter_scribe.plan import plan

JOBS = Path(__file__).resolve().parents[3] / "testdata" / "scribe"


@pytest.fixture(autouse=True)
def provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SARVAM_API_KEY", "GEMINI_API_KEY", "NVIDIA_API_KEY"):
        monkeypatch.setenv(name, "test-only-not-a-key")


def job(name: str) -> bytes:
    return (JOBS / name).read_bytes().strip()


def test_the_default_scribe_writes_with_sarvam_with_thinking_off() -> None:
    p = plan(load(job("hindi-scribe-job.json")), "dafter-scribe")
    writer = p.writer.build()
    assert isinstance(writer, SarvamLLM)
    assert writer.model == "sarvam-105b"
    assert p.judge is not None and isinstance(p.judge.build(), SarvamLLM)


@pytest.mark.parametrize(
    ("name", "base_url", "writer_model", "judge_model"),
    [
        (
            "hindi-scribe-gemini-job.json",
            "https://generativelanguage.googleapis.com/v1beta/openai",
            "gemini-3.5-flash-lite",
            "gemini-3.6-flash",
        ),
        (
            "hindi-scribe-nvidia-job.json",
            "https://integrate.api.nvidia.com/v1",
            "openai/gpt-oss-120b",
            "openai/gpt-oss-120b",
        ),
    ],
)
def test_a_profile_selects_a_free_model_through_openai_compat(
    name: str, base_url: str, writer_model: str, judge_model: str
) -> None:
    p = plan(load(job(name)), "dafter-scribe")
    assert p.judge is not None
    for model, want in ((p.writer.build(), writer_model), (p.judge.build(), judge_model)):
        assert isinstance(model, CompatLLM)
        assert str(model._client.base_url).rstrip("/") == base_url
        assert model.model == want


@pytest.mark.parametrize(
    "name",
    ["hindi-scribe-job.json", "hindi-scribe-gemini-job.json", "hindi-scribe-nvidia-job.json"],
)
def test_the_voice_agent_runs_every_scribe_job_unchanged(name: str) -> None:
    assert agent_plan(load(job(name)), "dafter-py").config.scribe.enabled
