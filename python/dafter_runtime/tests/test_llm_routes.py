from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, cast

import pytest
from dafter_core.config import ProviderRef
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError
from dafter_core.hashing import seal
from dafter_providers import VENDORS
from dafter_providers.openai_compat.client import CompatLLM
from dafter_runtime.control import ControlPlane
from dafter_runtime.cost import model_name, provider_name
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.stages import build
from dafter_runtime.worker import built, served

ROOT = Path(__file__).resolve().parents[3]
CATALOG = ROOT / "go" / "cmd" / "dafter-control" / "catalog.json"
GROQ_JOB = ROOT / "testdata" / "agent" / "hindi-groq-webrtc-job.json"
FOCUS = ("hi", "en-IN", "kn-IN", "mr-IN", "te-IN")


@pytest.fixture(autouse=True)
def provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SARVAM_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(name, "test-only-not-a-key")


def routes() -> dict[str, Any]:
    table: dict[str, Any] = json.loads(CATALOG.read_text(encoding="utf-8"))["llms"]
    return table


def test_the_pinned_groq_job_plans_groq_for_the_llm_and_sarvam_around_it() -> None:
    cfg = load(GROQ_JOB.read_bytes().strip())
    p = plan(cfg, "dafter-py")
    assert cfg.llm == "groq/qwen/qwen3.8-27b"
    assert (p.stt.name, p.llm.name, p.tts.name) == ("sarvam", "groq", "sarvam")
    assert p.llm.llm is not None and p.pipeline.llm is not None
    made = p.llm.llm(p.pipeline.llm)
    assert isinstance(made, CompatLLM)
    assert (made.provider, made.model) == ("groq", "qwen/qwen3.8-27b")
    assert made._opts.reasoning_effort == "none"


def groq_plan() -> Plan:
    doc = json.loads(GROQ_JOB.read_bytes())
    doc["agent"]["pipeline"]["tts"]["options"]["prewarm"] = False
    sealed, _ = seal(json.dumps(doc))
    return plan(load(sealed), "dafter-py")


class Control:
    def __init__(self) -> None:
        self.refusals: list[tuple[str, DafterError]] = []

    async def report_refusal(self, session_id: str, exc: DafterError) -> None:
        self.refusals.append((session_id, exc))


def test_a_route_whose_key_is_missing_is_refused_to_the_control_plane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GROQ_API_KEY")
    control = Control()
    with pytest.raises(DafterError) as caught:
        asyncio.run(built(groq_plan(), cast(ControlPlane, control)))
    assert caught.value.code is ErrorCode.AUTHENTICATION_FAILED
    assert control.refusals == [("s_7f3a9c21", caught.value)]
    assert "GROQ_API_KEY" in caught.value.message


def test_the_worker_names_the_llm_it_built_and_the_route_that_chose_it() -> None:
    p = groq_plan()

    async def run() -> dict[str, str]:
        stages = build(p)
        named = served(p, stages)
        for stage in (stages.stt, stages.llm, stages.tts):
            await stage.aclose()
        return named

    assert asyncio.run(run()) == {
        "llm": "groq/qwen/qwen3.8-27b",
        "llm_route": "groq/qwen/qwen3.8-27b",
    }


@pytest.mark.parametrize("route", sorted(routes()))
def test_every_catalog_route_builds_and_reports_usage_under_its_own_name(route: str) -> None:
    ref = ProviderRef.from_dict(routes()[route])
    vendor = VENDORS[ref.provider]
    assert set(FOCUS) <= vendor.languages
    assert vendor.llm is not None
    made = vendor.llm(ref)
    assert f"{provider_name(made.provider)}/{model_name(made.model)}" == route


@pytest.mark.parametrize("route", sorted(r for r in routes() if not r.startswith("sarvam/")))
def test_every_compat_route_asks_for_the_least_thinking_its_endpoint_allows(route: str) -> None:
    options = routes()[route]["options"]
    if route.startswith("groq/"):
        assert options["reasoningEffort"] == ("none" if "qwen" in route else "low")
    elif route.startswith("google/"):
        thinking = {"google": {"thinking_config": {"thinking_level": "minimal"}}}
        assert options["extraBody"] == {"extra_body": thinking}
    elif route == "openrouter/liquid/lfm-2.5-2.6b:free":
        assert options["extraBody"] == {"reasoning": {"effort": "low"}}
    else:
        assert options["extraBody"] == {"reasoning": {"enabled": False}}
