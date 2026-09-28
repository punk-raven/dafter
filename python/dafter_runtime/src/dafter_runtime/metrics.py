from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import Decimal

from dafter_core.config import ProviderRef
from dafter_core.enums import Stage
from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram

from .cost import UNITS, Item, model_name, provider_name
from .plan import Plan
from .timing import PAYLOAD_FIELDS, SERIAL_WAIT_MS, TurnTiming

PORT_ENV = "DAFTER_METRICS_PORT"
MULTIPROC_ENV = "PROMETHEUS_MULTIPROC_DIR"

LAYER_BUCKETS = (
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

PLACE = ("language", "channel")
PIPELINE = (*PLACE, "stt", "llm", "tts")
SPEND = (*PLACE, "stage", "provider", "model")
UNPRICED = (*SPEND, "unit")

ItemKey = tuple[str, str, str, str]


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


def vendor(ref: ProviderRef | None) -> str:
    if ref is None:
        return "none"
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


class SessionMetrics:
    def __init__(self, metrics: WorkerMetrics, p: Plan) -> None:
        cfg = p.config
        self._metrics = metrics
        self._place = (cfg.language, str(cfg.channel))
        self._pipeline = (
            *self._place,
            vendor(p.pipeline.stt),
            vendor(p.pipeline.llm),
            vendor(p.pipeline.tts),
        )
        self._spent: dict[ItemKey, Decimal] = {}
        self._zero(p)

    def _zero(self, p: Plan) -> None:
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


WORKER = WorkerMetrics()
