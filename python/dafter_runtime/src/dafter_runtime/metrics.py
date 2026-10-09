from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from dafter_core.config import ProviderRef
from dafter_core.enums import Stage
from dafter_core.errors import DafterError
from dafter_core.versioning import version_label
from livekit.agents import AgentSession
from livekit.agents.voice.events import AgentFalseInterruptionEvent, SpeechCreatedEvent
from livekit.agents.voice.speech_handle import SpeechHandle
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram

from .cost import UNITS, Item, model_name, provider_name
from .plan import Plan
from .timing import PAYLOAD_FIELDS, SERIAL_WAIT_MS, TurnTiming

PORT_ENV = "DAFTER_METRICS_PORT"
MULTIPROC_ENV = "PROMETHEUS_MULTIPROC_DIR"

LAYER_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.2,
    0.3,
    0.4,
    0.5,
    0.65,
    0.8,
    1.0,
    1.25,
    1.5,
    2.0,
    2.5,
    3.0,
    4.0,
    6.0,
    10.0,
)
SESSION_COST_BUCKETS = (0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 25.0, 50.0, 100.0)

PLACE = ("language", "channel", "version")
PIPELINE = (*PLACE, "stt", "llm", "tts")
SPEND = (*PLACE, "stage", "provider", "model")
UNPRICED = (*SPEND, "unit")
FAILING = (*PLACE, "component", "vendor")
FAILING_STAGES = (Stage.STT, Stage.LLM, Stage.TTS, Stage.CONTROL)
NO_VENDOR = "none"
PROGRAMMATIC = "programmatic"

ItemKey = tuple[str, str, str, str]


class Moment(StrEnum):
    INTERRUPTION = "interruption"
    FALSE_INTERRUPTION = "false_interruption"
    BACKCHANNEL_SUPPRESSED = "backchannel_suppressed"
    BACKCHANNEL_ANSWERED = "backchannel_answered"
    FILLER_PLAYED = "filler_played"
    PROVIDER_SWITCHED = "provider_switched"


MOMENT_SERIES = {
    Moment.INTERRUPTION: (
        "dafter_agent_interruptions",
        "Agent replies the user's voice cut off: audio activity or a committed user turn, never "
        "code stopping the reply.",
    ),
    Moment.FALSE_INTERRUPTION: (
        "dafter_agent_false_interruptions",
        "Times the agent stopped or paused for a voice over it and no user turn followed.",
    ),
    Moment.BACKCHANNEL_SUPPRESSED: (
        "dafter_agent_backchannels_suppressed",
        "Acknowledgements said over the agent's reply that were held back instead of "
        "interrupting it.",
    ),
    Moment.BACKCHANNEL_ANSWERED: (
        "dafter_agent_backchannel_answers",
        "Held-back acknowledgements that turned out to answer the agent's question and were "
        "released as a user turn.",
    ),
    Moment.FILLER_PLAYED: (
        "dafter_agent_filler_plays",
        "Agent turns that played a filler before the reply.",
    ),
    Moment.PROVIDER_SWITCHED: (
        "dafter_agent_provider_switches",
        "Times an LLM or TTS stage marked a provider unavailable and moved along its fallback "
        "chain.",
    ),
}


class WorkerMetrics:
    def __init__(self, registry: CollectorRegistry = REGISTRY) -> None:
        self.layers = Histogram(
            "dafter_agent_turn_layer_seconds",
            "Where an agent turn's latency went, one series per layer, measured in the worker.",
            ["layer", *PIPELINE],
            buckets=LAYER_BUCKETS,
            registry=registry,
        )
        self.serial_checked = Counter(
            "dafter_agent_serial_checked_turns",
            "Agent turns the serial rule could judge: both LLM layers were measured.",
            PIPELINE,
            registry=registry,
        )
        self.serial = Counter(
            "dafter_agent_serial_turns",
            "Agent turns that went serial: the first sentence reached TTS more than "
            f"{SERIAL_WAIT_MS} ms after the LLM's first token.",
            PIPELINE,
            registry=registry,
        )
        self.cost = Counter(
            "dafter_agent_cost_inr",
            "What agent sessions have spent in INR, counted as each turn's usage is priced.",
            SPEND,
            registry=registry,
        )
        self.session_cost = Histogram(
            "dafter_agent_session_cost_inr",
            "What one agent session cost in INR, observed when it ends. A floor while any of "
            "its items is unpriced.",
            PLACE,
            buckets=SESSION_COST_BUCKETS,
            registry=registry,
        )
        self.unpriced = Counter(
            "dafter_agent_unpriced_items",
            "Usage items the price table has no row for, counted once per session at its end.",
            UNPRICED,
            registry=registry,
        )
        self.moments = {
            moment: Counter(name, about, PLACE, registry=registry)
            for moment, (name, about) in MOMENT_SERIES.items()
        }
        self.provider_errors = Counter(
            "dafter_agent_provider_errors",
            "Pipeline stage failures a vendor adapter classified, retried or given up, by the "
            "component that failed and the vendor behind it.",
            FAILING,
            registry=registry,
        )


def vendor(ref: ProviderRef | None) -> str:
    if ref is None:
        return NO_VENDOR
    return f"{provider_name(ref.provider)}/{model_name(ref.model or '')}"


@dataclass(frozen=True, slots=True)
class Exposition:
    port: int
    multiproc_dir: str


def exposition(env: Mapping[str, str] = os.environ) -> Exposition | None:
    raw = env.get(PORT_ENV, "").strip()
    if not raw:
        return None
    if not raw.isdigit() or not 0 < int(raw) < 65536:
        raise ValueError(f"{PORT_ENV} must be a TCP port number, got {raw!r}")
    shared = env.get(MULTIPROC_ENV) or os.path.join(
        tempfile.gettempdir(), f"dafter-runtime-metrics-{os.getpid()}"
    )
    return Exposition(port=int(raw), multiproc_dir=shared)


def cut_by_user(speech: SpeechHandle) -> bool:
    if not speech.interrupted:
        return False
    return getattr(speech, "_interrupt_source", None) not in (None, PROGRAMMATIC)


class SessionMetrics:
    def __init__(
        self, metrics: WorkerMetrics, p: Plan, session: AgentSession[Any] | None = None
    ) -> None:
        cfg = p.config
        self._metrics = metrics
        self._place = (cfg.language, str(cfg.channel), version_label(cfg.version))
        self._vendors = {
            Stage.STT: vendor(p.pipeline.stt),
            Stage.LLM: vendor(p.pipeline.llm),
            Stage.TTS: vendor(p.pipeline.tts),
        }
        self._pipeline = (*self._place, *self._vendors.values())
        primaries = {
            Stage.LLM: p.pipeline.llm.provider if p.pipeline.llm else None,
            Stage.TTS: p.pipeline.tts.provider if p.pipeline.tts else None,
        }
        self._fallbacks = {
            (stage, failover.ref.provider): vendor(failover.ref)
            for stage, chain in p.fallbacks.items()
            for failover in chain
            if failover.ref.provider != primaries.get(stage)
        }
        self._spent: dict[ItemKey, Decimal] = {}
        self._zero(p)
        if session is not None:
            self._follow(session)
        SERVING.session = self

    def _follow(self, session: AgentSession[Any]) -> None:
        def speech_created(ev: SpeechCreatedEvent) -> None:
            ev.speech_handle.add_done_callback(self._speech_done)

        def false_interruption(_: AgentFalseInterruptionEvent) -> None:
            self.count(Moment.FALSE_INTERRUPTION)

        session.on("speech_created", speech_created)
        session.on("agent_false_interruption", false_interruption)

    def _speech_done(self, speech: SpeechHandle) -> None:
        if cut_by_user(speech):
            self.count(Moment.INTERRUPTION)

    def _zero(self, p: Plan) -> None:
        for counter in self._metrics.moments.values():
            counter.labels(*self._place)
        for stage in FAILING_STAGES:
            failing = self._vendors.get(stage, NO_VENDOR)
            self._metrics.provider_errors.labels(*self._place, str(stage), failing)
        for (stage, _), failing in self._fallbacks.items():
            self._metrics.provider_errors.labels(*self._place, str(stage), failing)
        for layer in PAYLOAD_FIELDS:
            self._metrics.layers.labels(layer, *self._pipeline)
        self._metrics.serial_checked.labels(*self._pipeline)
        self._metrics.serial.labels(*self._pipeline)
        self._metrics.session_cost.labels(*self._place)
        stages = (
            (Stage.STT, p.pipeline.stt),
            (Stage.LLM, p.pipeline.llm),
            (Stage.TTS, p.pipeline.tts),
        )
        for stage, ref in stages:
            if ref is None:
                continue
            spend = (
                *self._place,
                str(stage),
                provider_name(ref.provider),
                model_name(ref.model or ""),
            )
            self._metrics.cost.labels(*spend)
            for unit in UNITS[stage]:
                self._metrics.unpriced.labels(*spend, str(unit))

    def turn(self, timing: TurnTiming) -> None:
        for layer, seconds in timing.seconds.items():
            self._metrics.layers.labels(layer, *self._pipeline).observe(seconds)
        serial = timing.serial()
        if serial is not None:
            self._metrics.serial_checked.labels(*self._pipeline).inc()
        if serial:
            self._metrics.serial.labels(*self._pipeline).inc()
        if timing.filler:
            self.count(Moment.FILLER_PLAYED)

    def count(self, moment: Moment) -> None:
        self._metrics.moments[moment].labels(*self._place).inc()

    def failing(self, stage: Stage, err: DafterError) -> str:
        named = err.provider.name if err.provider is not None else None
        return self._fallbacks.get((stage, named or ""), self._vendors.get(stage, NO_VENDOR))

    def degraded(self, stage: Stage, err: DafterError, recoverable: bool) -> dict[str, Any]:
        failing = self.failing(stage, err)
        self._metrics.provider_errors.labels(*self._place, str(stage), failing).inc()
        return {"error": err.to_dict(), "recoverable": recoverable}

    def usage(self, items: Iterable[Item]) -> None:
        for item in items:
            if item.cost is None:
                continue
            key = (str(item.stage), item.provider, item.model, str(item.unit))
            grown = item.cost - self._spent.get(key, Decimal(0))
            if grown > 0:
                labels = (*self._place, str(item.stage), item.provider, item.model)
                self._metrics.cost.labels(*labels).inc(float(grown))
                self._spent[key] = item.cost

    def closed(self, items: list[Item]) -> None:
        self.usage(items)
        total = sum((i.cost for i in items if i.cost is not None), Decimal(0))
        self._metrics.session_cost.labels(*self._place).observe(float(total))
        for item in items:
            if item.cost is None:
                labels = (*self._place, str(item.stage), item.provider, item.model, str(item.unit))
                self._metrics.unpriced.labels(*labels).inc()
        if SERVING.session is self:
            SERVING.session = None


@dataclass(slots=True)
class Serving:
    session: SessionMetrics | None = None


SERVING = Serving()


def counted(moment: Moment) -> None:
    if SERVING.session is not None:
        SERVING.session.count(moment)


WORKER = WorkerMetrics()
