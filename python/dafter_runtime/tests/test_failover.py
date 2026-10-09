from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from dafter_core.config import Pipeline, ProviderRef
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError, ProviderContext
from dafter_core.hashing import seal
from dafter_core.pipeline import Fallback
from dafter_providers import fallback
from dafter_providers.openai_compat.client import CompatLLM
from dafter_runtime.metrics import SessionMetrics, WorkerMetrics
from dafter_runtime.plan import Plan, failovers, load, plan
from dafter_runtime.stages import Stages, build
from livekit.agents import llm as lk_llm
from livekit.agents import stt as lk_stt
from livekit.agents import tts as lk_tts
from prometheus_client import CollectorRegistry

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
GROQ = ProviderRef(
    provider="groq",
    model="qwen/qwen3.8-27b",
    credential_ref="secret://tenants/t_9c21a4be/groq/api-key",
    options={"reasoningEffort": "none"},
)
GROQ_DOC = {
    "provider": "groq",
    "model": "qwen/qwen3.8-27b",
    "credentialRef": "secret://tenants/t_9c21a4be/groq/api-key",
    "options": {"reasoningEffort": "none"},
}


@pytest.fixture(autouse=True)
def provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SARVAM_API_KEY", "GROQ_API_KEY"):
        monkeypatch.setenv(name, "test-only-not-a-key")
    monkeypatch.delenv(fallback.FAULT_ENV, raising=False)


def sarvam_plan(fallback_llm: list[dict[str, Any]] | None = None) -> Plan:
    doc = json.loads(JOB.read_bytes())
    for stage in ("llm", "tts"):
        doc["agent"]["pipeline"][stage]["options"]["prewarm"] = False
    doc["agent"]["pipeline"]["fallback"] = {"llm": fallback_llm or [], "tts": []}
    sealed, _ = seal(json.dumps(doc))
    return plan(load(sealed), "dafter-py")


def built(p: Plan, faults: frozenset[Stage] = frozenset()) -> Stages:
    async def run() -> Stages:
        stages = build(p, faults)
        for stage in (stages.stt, stages.llm, stages.tts):
            await stage.aclose()
        return stages

    return asyncio.run(run())


def llm_chain(*refs: ProviderRef) -> Pipeline:
    primary = ProviderRef(provider="sarvam", model="sarvam-105b")
    return Pipeline(llm=primary, fallback=Fallback(llm=refs))


def test_a_planned_llm_fallback_names_an_openai_compatible_vendor() -> None:
    [failover] = failovers(llm_chain(GROQ), "hi")[Stage.LLM]
    assert failover.vendor.name == "groq"
    assert failover.ref is GROQ


def test_a_fallback_identical_to_the_primary_is_skipped() -> None:
    same = ProviderRef(provider="sarvam", model="sarvam-105b")
    assert failovers(llm_chain(same), "hi") == {}


def test_a_fallback_that_does_not_serve_the_language_is_refused_by_pointer() -> None:
    with pytest.raises(DafterError) as caught:
        failovers(llm_chain(GROQ), "xx-XX")
    assert caught.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert caught.value.details == ("at '/agent/pipeline/fallback/llm/0': not declared by groq",)


def test_a_fallback_without_a_registered_stage_is_refused_by_pointer() -> None:
    with pytest.raises(DafterError) as caught:
        failovers(Pipeline(fallback=Fallback(tts=(GROQ,))), "hi")
    assert caught.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert caught.value.details[0].startswith("at '/agent/pipeline/fallback/tts/0/provider'")


def test_the_plan_reads_its_fallbacks_from_the_pipeline() -> None:
    p = sarvam_plan([GROQ_DOC])
    [failover] = p.fallbacks[Stage.LLM]
    assert (failover.vendor.name, failover.ref.model) == ("groq", "qwen/qwen3.8-27b")
    assert Stage.TTS not in p.fallbacks


def test_an_empty_slot_builds_the_stages_unwrapped() -> None:
    stages = built(sarvam_plan())
    assert not isinstance(stages.stt, lk_stt.FallbackAdapter)
    assert not isinstance(stages.llm, lk_llm.FallbackAdapter)
    assert not isinstance(stages.tts, lk_tts.FallbackAdapter)


def test_a_planned_llm_fallback_wraps_the_primary_first() -> None:
    stages = built(sarvam_plan([GROQ_DOC]))
    assert isinstance(stages.llm, lk_llm.FallbackAdapter)
    primary, second = stages.llm._llm_instances
    assert primary.provider != "groq"
    assert isinstance(second, CompatLLM)


def test_a_fallback_whose_key_is_missing_is_left_out_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GROQ_API_KEY")
    stages = built(sarvam_plan([GROQ_DOC]))
    assert not isinstance(stages.llm, lk_llm.FallbackAdapter)


def test_an_injected_fault_replaces_only_the_named_primary() -> None:
    stages = built(sarvam_plan(), frozenset({Stage.TTS}))
    assert isinstance(stages.tts, fallback.FailoverTTS)
    [faulty] = stages.tts._tts_instances
    assert isinstance(faulty, fallback.FaultyTTS)
    assert not isinstance(stages.llm, lk_llm.FallbackAdapter)


def test_speech_to_text_is_never_wrapped() -> None:
    stages = built(sarvam_plan([GROQ_DOC]), frozenset({Stage.LLM}))
    assert not isinstance(stages.stt, lk_stt.FallbackAdapter)


def test_a_failing_fallback_is_counted_under_its_own_vendor() -> None:
    registry = CollectorRegistry()
    session = SessionMetrics(WorkerMetrics(registry), sarvam_plan([GROQ_DOC]))
    groq_failed = ProviderContext("groq")
    err = DafterError(ErrorCode.PROVIDER_TIMEOUT, "groq llm failed", Stage.LLM, groq_failed)
    session.degraded(Stage.LLM, err, True)
    labels = {"language": "hi", "channel": "webrtc", "version": "nivya-v1", "component": "llm"}
    groq = {**labels, "vendor": "groq/qwen/qwen3.8-27b"}
    assert registry.get_sample_value("dafter_agent_provider_errors_total", groq) == 1
    sarvam = {**labels, "vendor": "sarvam/sarvam-105b"}
    assert registry.get_sample_value("dafter_agent_provider_errors_total", sarvam) == 0
