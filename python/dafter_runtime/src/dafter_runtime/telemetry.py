from __future__ import annotations

import os

from dafter_core.config import ResolvedSessionConfig
from livekit.agents.telemetry import set_tracer_provider
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

ENDPOINT_ENV = "OTEL_EXPORTER_OTLP_ENDPOINT"
SERVICE = "dafter-runtime"


def span_attributes(cfg: ResolvedSessionConfig) -> dict[str, str]:
    attrs = {
        "dafter.session_id": cfg.session_id,
        "dafter.tenant_id": cfg.tenant_id,
        "dafter.language": cfg.language,
        "dafter.channel": str(cfg.channel),
    }
    if cfg.config_hash:
        attrs["dafter.config_hash"] = cfg.config_hash
    return attrs


def install(cfg: ResolvedSessionConfig) -> TracerProvider | None:
    if not os.environ.get(ENDPOINT_ENV):
        return None
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: SERVICE}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    set_tracer_provider(provider, metadata=dict(span_attributes(cfg)), allow_pii=False)
    return provider
