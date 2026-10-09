from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import textwrap
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from dafter_core.enums import ErrorCode, Stage, UsageUnit
from dafter_core.errors import DafterError, ProviderContext
from dafter_core.hashing import seal
from dafter_runtime.barge_in import BargeIn, Resume
from dafter_runtime.cost import Item, load_prices, priced
from dafter_runtime.metrics import (
    MOMENT_SERIES,
    MULTIPROC_ENV,
    PORT_ENV,
    SERVING,
    Moment,
    SessionMetrics,
    WorkerMetrics,
    counted,
    exposition,
)
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.timing import PAYLOAD_FIELDS, TurnTiming
from livekit.agents.metrics import AgentSessionUsage, LLMModelUsage, STTModelUsage, TTSModelUsage
from prometheus_client import CollectorRegistry
from prometheus_client.multiprocess import MultiProcessCollector

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
VERSION = "nivya-v1"
PIPELINE = {
    "language": "hi",
    "channel": "webrtc",
    "version": VERSION,
    "stt": "sarvam/saaras:v3-realtime",
    "llm": "sarvam/sarvam-105b",
    "tts": "sarvam/bulbul:v3",
}
PLACE = {"language": "hi", "channel": "webrtc", "version": VERSION}
LLM = {**PLACE, "stage": "llm", "provider": "sarvam", "model": "sarvam-105b"}


def hindi_plan() -> Plan:
    return plan(load(JOB.read_bytes().strip()), "dafter-py")


def fresh() -> tuple[CollectorRegistry, SessionMetrics]:
    registry = CollectorRegistry()
    return registry, SessionMetrics(WorkerMetrics(registry), hindi_plan())


def llm_tokens(cost: str | None, unit: UsageUnit = UsageUnit.INPUT_TOKEN) -> Item:
    return Item(Stage.LLM, "sarvam", "sarvam-105b", unit, 100.0, Decimal(cost) if cost else None)


def test_every_measured_layer_is_observed_under_the_pipeline_that_ran() -> None:
    registry, session = fresh()
    session.turn(TurnTiming(turn=1, interrupted=False, seconds={"e2e_latency": 0.9}))
    session.turn(
        TurnTiming(turn=2, interrupted=True, seconds={"e2e_latency": 1.7, "llm_node_ttft": 0.4})
    )
    e2e = {"layer": "e2e_latency", **PIPELINE}
    assert registry.get_sample_value("dafter_agent_turn_layer_seconds_count", e2e) == 2
    assert registry.get_sample_value("dafter_agent_turn_layer_seconds_sum", e2e) == 2.6
    bucket = {**e2e, "le": "1.5"}
    assert registry.get_sample_value("dafter_agent_turn_layer_seconds_bucket", bucket) == 1
    ttft = {"layer": "llm_node_ttft", **PIPELINE}
    assert registry.get_sample_value("dafter_agent_turn_layer_seconds_count", ttft) == 1


def test_serial_turns_are_counted_beside_the_turns_the_rule_could_judge() -> None:
    registry, session = fresh()
    judged = {"llm_node_ttft": 0.3}
    session.turn(TurnTiming(0, False, {**judged, "llm_node_ttfs": 0.5}))
    session.turn(TurnTiming(1, False, {**judged, "llm_node_ttfs": 2.3}))
    session.turn(TurnTiming(2, True, judged))
    assert registry.get_sample_value("dafter_agent_serial_checked_turns_total", PIPELINE) == 2
    assert registry.get_sample_value("dafter_agent_serial_turns_total", PIPELINE) == 1


def test_no_label_names_a_session() -> None:
    registry, session = fresh()
    session.turn(TurnTiming(turn=0, interrupted=False, seconds={"e2e_latency": 0.9}))
    session.closed([llm_tokens("0.5")])
    session_id = hindi_plan().config.session_id
    for family in registry.collect():
        for sample in family.samples:
            assert session_id not in sample.labels.values()


def test_running_usage_counts_only_what_each_report_adds() -> None:
    registry, session = fresh()
    session.usage([llm_tokens("0.25")])
    session.usage([llm_tokens("0.25")])
    session.usage([llm_tokens("0.75"), llm_tokens("0.1", UsageUnit.OUTPUT_TOKEN)])
    assert registry.get_sample_value("dafter_agent_cost_inr_total", LLM) == pytest.approx(0.85)


def test_a_closed_session_observes_its_cost_and_counts_what_had_no_price() -> None:
    registry, session = fresh()
    session.usage([llm_tokens("0.25")])
    tts = Item(Stage.TTS, "sarvam", "bulbul:v3", UsageUnit.CHARACTER, 400.0, None)
    session.closed([llm_tokens("0.5"), tts])
    assert registry.get_sample_value("dafter_agent_cost_inr_total", LLM) == 0.5
    assert registry.get_sample_value("dafter_agent_session_cost_inr_count", PLACE) == 1
    assert registry.get_sample_value("dafter_agent_session_cost_inr_sum", PLACE) == 0.5
    unpriced = {**PLACE, "stage": "tts", "provider": "sarvam", "model": "bulbul:v3"}
    labels = {**unpriced, "unit": "character"}
    assert registry.get_sample_value("dafter_agent_unpriced_items_total", labels) == 1


def test_metrics_are_exposed_only_when_a_port_is_set() -> None:
    assert exposition({}) is None
    assert exposition({PORT_ENV: " "}) is None
    shared = exposition({PORT_ENV: "9464", MULTIPROC_ENV: "/var/run/dafter-metrics"})
    assert shared is not None
    assert (shared.port, shared.multiproc_dir) == (9464, "/var/run/dafter-metrics")
    own = exposition({PORT_ENV: "9464"})
    assert own is not None and own.multiproc_dir.endswith(f"dafter-runtime-metrics-{os.getpid()}")


@pytest.mark.parametrize("port", ["http", "0", "65536", "-1", "94.64"])
def test_an_unusable_port_stops_the_worker(port: str) -> None:
    with pytest.raises(ValueError, match=PORT_ENV):
        exposition({PORT_ENV: port})


def test_a_job_process_turn_reaches_the_workers_metrics_endpoint(tmp_path: Path) -> None:
    job_process = textwrap.dedent(
        f"""
        from pathlib import Path
        from dafter_runtime.metrics import WORKER, SessionMetrics
        from dafter_runtime.plan import load, plan
        from dafter_runtime.timing import TurnTiming

        p = plan(load(Path({str(JOB)!r}).read_bytes().strip()), "dafter-py")
        timing = TurnTiming(turn=0, interrupted=False, seconds={{"e2e_latency": 0.9}})
        SessionMetrics(WORKER, p).turn(timing)
        """
    )
    env = {**os.environ, MULTIPROC_ENV: str(tmp_path)}
    subprocess.run([sys.executable, "-c", job_process], env=env, check=True, timeout=120)
    registry = CollectorRegistry()
    MultiProcessCollector(registry, path=str(tmp_path))  # type: ignore[no-untyped-call]
    e2e = {"layer": "e2e_latency", **PIPELINE}
    assert registry.get_sample_value("dafter_agent_turn_layer_seconds_count", e2e) == 1


def test_every_series_a_session_can_produce_reads_zero_before_its_first_turn() -> None:
    registry, _ = fresh()
    for layer in PAYLOAD_FIELDS:
        labels = {"layer": layer, **PIPELINE}
        assert registry.get_sample_value("dafter_agent_turn_layer_seconds_count", labels) == 0
    assert registry.get_sample_value("dafter_agent_serial_checked_turns_total", PIPELINE) == 0
    assert registry.get_sample_value("dafter_agent_serial_turns_total", PIPELINE) == 0
    assert registry.get_sample_value("dafter_agent_session_cost_inr_count", PLACE) == 0
    spends = {
        "stt": ("saaras:v3-realtime", ("audio_second",)),
        "llm": ("sarvam-105b", ("input_token", "output_token")),
        "tts": ("bulbul:v3", ("character",)),
    }
    for stage, (model, units) in spends.items():
        spend = {**PLACE, "stage": stage, "provider": "sarvam", "model": model}
        assert registry.get_sample_value("dafter_agent_cost_inr_total", spend) == 0
        for unit in units:
            labels = {**spend, "unit": unit}
            assert registry.get_sample_value("dafter_agent_unpriced_items_total", labels) == 0


def test_a_job_process_exposes_zero_series_before_any_turn(tmp_path: Path) -> None:
    job_process = textwrap.dedent(
        f"""
        from pathlib import Path
        from dafter_runtime.metrics import WORKER, SessionMetrics
        from dafter_runtime.plan import load, plan

        SessionMetrics(WORKER, plan(load(Path({str(JOB)!r}).read_bytes().strip()), "dafter-py"))
        """
    )
    env = {**os.environ, MULTIPROC_ENV: str(tmp_path)}
    subprocess.run([sys.executable, "-c", job_process], env=env, check=True, timeout=120)
    registry = CollectorRegistry()
    MultiProcessCollector(registry, path=str(tmp_path))  # type: ignore[no-untyped-call]
    e2e = {"layer": "e2e_latency", **PIPELINE}
    assert registry.get_sample_value("dafter_agent_turn_layer_seconds_count", e2e) == 0
    assert registry.get_sample_value("dafter_agent_serial_turns_total", PIPELINE) == 0
    assert registry.get_sample_value("dafter_agent_session_cost_inr_count", PLACE) == 0
    assert registry.get_sample_value("dafter_agent_cost_inr_total", LLM) == 0


def series(registry: CollectorRegistry) -> set[tuple[str, tuple[tuple[str, str], ...]]]:
    return {
        (sample.name, tuple(sorted(sample.labels.items())))
        for family in registry.collect()
        for sample in family.samples
        if not sample.name.endswith("_created")
    }


def test_a_session_adds_no_series_beyond_the_ones_it_started_with() -> None:
    registry, session = fresh()
    before = series(registry)
    usage = AgentSessionUsage(
        model_usage=[
            LLMModelUsage(provider="Sarvam", model="sarvam-105b", input_tokens=90, output_tokens=9),
            TTSModelUsage(provider="Sarvam", model="bulbul:v3", characters_count=40),
            STTModelUsage(provider="Sarvam", model="saaras:v3-realtime", audio_duration=4.0),
        ]
    )
    seconds = {layer: 0.5 for layer in PAYLOAD_FIELDS}
    session.turn(TurnTiming(turn=0, interrupted=False, seconds={**seconds, "llm_node_ttfs": 2.0}))
    session.usage(priced(usage, load_prices()))
    session.closed(priced(usage, {}))
    assert series(registry) == before


def test_prometheus_scrapes_the_worker_at_the_pinned_compose_gateway() -> None:
    root = JOB.parents[2]
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    scrape = (root / "deploy" / "prometheus.yml").read_text(encoding="utf-8")
    gateway = re.search(r"^\s+gateway: (\S+)$", compose, re.MULTILINE)
    port = re.search(rf'{PORT_ENV}: "(\d+)"', compose)
    job = scrape.split("job_name: dafter-agent", 1)[1]
    target = re.search(r'targets: \["([^"]+)"\]', job)
    assert gateway and port and target
    assert target.group(1) == f"{gateway.group(1)}:{port.group(1)}"


def test_a_layer_of_a_few_milliseconds_lands_in_its_own_bucket() -> None:
    registry, session = fresh()
    session.turn(TurnTiming(turn=0, interrupted=False, seconds={"end_of_turn_delay": 0.003}))
    labels = {"layer": "end_of_turn_delay", **PIPELINE}
    bucket = "dafter_agent_turn_layer_seconds_bucket"
    assert registry.get_sample_value(bucket, {**labels, "le": "0.005"}) == 1
    for le in ("0.01", "0.025", "0.05"):
        assert registry.get_sample_value(bucket, {**labels, "le": le}) == 1


class FakeSession:
    def __init__(self) -> None:
        self.handlers: dict[str, Callable[[Any], None]] = {}

    def on(self, event: str, handler: Callable[[Any], None]) -> None:
        self.handlers[event] = handler

    def speech_ends(self, interrupted: bool, source: str | None) -> None:
        def add_done_callback(done: Callable[[Any], None]) -> None:
            done(speech)

        speech = SimpleNamespace(
            interrupted=interrupted,
            _interrupt_source=source,
            add_done_callback=add_done_callback,
        )
        self.handlers["speech_created"](SimpleNamespace(speech_handle=speech))


def moment_total(registry: CollectorRegistry, moment: Moment) -> float | None:
    return registry.get_sample_value(f"{MOMENT_SERIES[moment][0]}_total", PLACE)


def provider_errors(registry: CollectorRegistry, component: str, vendor: str) -> float | None:
    labels = {**PLACE, "component": component, "vendor": vendor}
    return registry.get_sample_value("dafter_agent_provider_errors_total", labels)


def test_every_quality_series_reads_zero_before_the_first_turn() -> None:
    registry, _ = fresh()
    for moment in Moment:
        assert moment_total(registry, moment) == 0
    for stage in ("stt", "llm", "tts"):
        assert provider_errors(registry, stage, PIPELINE[stage]) == 0
    assert provider_errors(registry, "control", "none") == 0


def test_a_moment_counted_anywhere_in_the_process_lands_on_the_serving_session() -> None:
    registry, session = fresh()
    counted(Moment.BACKCHANNEL_SUPPRESSED)
    counted(Moment.BACKCHANNEL_SUPPRESSED)
    counted(Moment.BACKCHANNEL_ANSWERED)
    assert moment_total(registry, Moment.BACKCHANNEL_SUPPRESSED) == 2
    assert moment_total(registry, Moment.BACKCHANNEL_ANSWERED) == 1
    session.closed([])
    assert SERVING.session is None
    counted(Moment.BACKCHANNEL_SUPPRESSED)
    assert moment_total(registry, Moment.BACKCHANNEL_SUPPRESSED) == 2


def test_a_turn_that_played_a_filler_is_counted() -> None:
    registry, session = fresh()
    session.turn(TurnTiming(turn=0, interrupted=False, seconds={}, filler=True))
    session.turn(TurnTiming(turn=1, interrupted=False, seconds={}))
    assert moment_total(registry, Moment.FILLER_PLAYED) == 1


def test_a_degraded_stage_is_counted_under_its_vendor_and_keeps_the_event_payload() -> None:
    registry, session = fresh()
    err = DafterError(ErrorCode.PROVIDER_TIMEOUT, "tts timed out", Stage.TTS, ProviderContext("x"))
    assert session.degraded(Stage.TTS, err, True) == {"error": err.to_dict(), "recoverable": True}
    session.degraded(Stage.CONTROL, err, False)
    assert provider_errors(registry, "tts", PIPELINE["tts"]) == 1
    assert provider_errors(registry, "control", "none") == 1
    assert provider_errors(registry, "llm", PIPELINE["llm"]) == 0


def test_only_a_reply_the_user_cut_off_counts_as_an_interruption() -> None:
    registry = CollectorRegistry()
    fake = FakeSession()
    SessionMetrics(WorkerMetrics(registry), hindi_plan(), cast(Any, fake))
    fake.speech_ends(interrupted=True, source="audio_activity")
    fake.speech_ends(interrupted=True, source="user_turn")
    fake.speech_ends(interrupted=True, source="programmatic")
    fake.speech_ends(interrupted=False, source=None)
    assert moment_total(registry, Moment.INTERRUPTION) == 2


def test_the_frameworks_false_interruption_is_counted() -> None:
    registry = CollectorRegistry()
    fake = FakeSession()
    SessionMetrics(WorkerMetrics(registry), hindi_plan(), cast(Any, fake))
    fake.handlers["agent_false_interruption"](SimpleNamespace(resumed=True))
    assert moment_total(registry, Moment.FALSE_INTERRUPTION) == 1


def test_a_paused_reply_resumed_with_no_words_is_a_false_interruption() -> None:
    registry, _ = fresh()
    timers: list[Callable[[], None]] = []

    def schedule(_: float, callback: Callable[[], None]) -> Any:
        timers.append(callback)
        return SimpleNamespace(cancel=lambda: None)

    barge_in = BargeIn(
        0.25,
        0,
        lambda: "p_4b81e0d7",
        lambda: None,
        lambda: 1000.0,
        schedule,
        resume=Resume(lambda: True, lambda: None, 2.0),
    )
    barge_in.speaking("p_4b81e0d7", 1000.0)
    timers.pop()()
    barge_in.quiet("p_4b81e0d7")
    timers.pop()()
    assert moment_total(registry, Moment.FALSE_INTERRUPTION) == 1


def test_quality_counting_adds_no_series_beyond_the_ones_a_session_started_with() -> None:
    registry, session = fresh()
    before = series(registry)
    for moment in Moment:
        session.count(moment)
    err = DafterError(ErrorCode.PROVIDER_TIMEOUT, "stt timed out", Stage.STT)
    for stage in (Stage.STT, Stage.LLM, Stage.TTS, Stage.CONTROL):
        session.degraded(stage, err, True)
    assert series(registry) == before


def versioned_plan(version: dict[str, Any] | None) -> Plan:
    doc = json.loads(JOB.read_bytes())
    doc.pop("version", None)
    if version is not None:
        doc["version"] = version
    sealed, _ = seal(json.dumps(doc))
    return plan(load(sealed), "dafter-py")


@pytest.mark.parametrize(
    ("version", "label"),
    [({"id": "support-v4", "candidate": True}, "support-v4"), (None, "none")],
    ids=["candidate", "unversioned"],
)
def test_every_series_carries_the_version_the_session_runs(
    version: dict[str, Any] | None, label: str
) -> None:
    registry = CollectorRegistry()
    session = SessionMetrics(WorkerMetrics(registry), versioned_plan(version))
    session.count(Moment.FILLER_PLAYED)
    place = {**PLACE, "version": label}
    assert registry.get_sample_value(f"{MOMENT_SERIES[Moment.FILLER_PLAYED][0]}_total", place) == 1
    e2e = {"layer": "e2e_latency", **PIPELINE, "version": label}
    assert registry.get_sample_value("dafter_agent_turn_layer_seconds_count", e2e) == 0
