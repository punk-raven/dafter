from __future__ import annotations

import logging
import os
from typing import Any

from dafter_core.enums import EncryptionMode, EventType, Stage
from dafter_core.errors import DafterError
from livekit import local_inference, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    AutoSubscribe,
    JobContext,
    JobProcess,
    JobRequest,
)
from livekit.agents.llm import ChatMessage
from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    CloseEvent,
    ConversationItemAddedEvent,
    ErrorEvent,
)
from livekit.agents.voice.room_io import AudioInputOptions, AudioOutputOptions, RoomOptions
from opentelemetry import trace

from . import telemetry
from .control import ControlPlane, encryption
from .cost import load_prices, priced, usage_payload
from .events import TOPIC, SessionEvents
from .metrics import WORKER, SessionMetrics, WorkerMetrics, exposition
from .plan import Plan, load, plan
from .stages import Stages, build
from .timing import Turns

POOL_ENV = "LIVEKIT_AGENT_NAME"
DEFAULT_POOL = "dafter-py"

PII_PREFIX = "lk.pii."
FRAMEWORK_LOGGER = "livekit.agents"

log = logging.getLogger("dafter.runtime")


class RedactTranscripts(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        for key in [k for k in record.__dict__ if k.startswith(PII_PREFIX)]:
            del record.__dict__[key]
        return True


def redact_framework_logs() -> None:
    framework = logging.getLogger(FRAMEWORK_LOGGER)
    if not any(isinstance(f, RedactTranscripts) for f in framework.filters):
        framework.addFilter(RedactTranscripts())


def pool() -> str:
    return os.environ.get(POOL_ENV) or DEFAULT_POOL


def refusal(exc: DafterError) -> dict[str, Any]:
    return {"code": str(exc.code), "reason": exc.message, "details": list(exc.details)}


async def refuse(control: ControlPlane | None, session_id: str, exc: DafterError) -> None:
    log.warning("job refused", extra=refusal(exc))
    if control is not None:
        await control.report_refusal(session_id, exc)


async def on_request(req: JobRequest) -> None:
    control = ControlPlane.from_env()
    try:
        plan(load(req.job.metadata), pool(), fetches_keys=control is not None)
    except DafterError as exc:
        await refuse(control, req.room.name, exc)
        await req.reject()
        return
    await req.accept(name="Dafter agent", attributes={"dafter.role": "agent"})


async def room_encryption(p: Plan, control: ControlPlane) -> rtc.E2EEOptions | None:
    if p.config.media.encryption.stated_mode is not EncryptionMode.E2EE:
        return None
    try:
        key = await control.session_key(p.config)
    except DafterError as exc:
        await refuse(control, p.config.session_id, exc)
        raise
    return encryption(key)


def room_options(p: Plan, tts_sample_rate: int) -> RoomOptions:
    stt_rate = int(p.pipeline.stt.options.get("sampleRate", 16000)) if p.pipeline.stt else 16000
    return RoomOptions(
        audio_input=AudioInputOptions(sample_rate=stt_rate),
        audio_output=AudioOutputOptions(sample_rate=tts_sample_rate),
        close_on_disconnect=True,
    )


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def watch(
    session: AgentSession[Any],
    p: Plan,
    events: SessionEvents,
    tracer: trace.Tracer,
    metrics: WorkerMetrics = WORKER,
) -> None:
    turns = Turns()
    prices = load_prices()
    recorder = SessionMetrics(metrics, p)

    def report_usage(final: bool) -> dict[str, Any]:
        items = priced(session.usage, prices)
        payload = usage_payload(items, final)
        events.emit(EventType.SESSION_USAGE, payload, current_trace_id())
        if final:
            recorder.closed(items)
        else:
            recorder.usage(items)
        return payload

    def state_changed(ev: AgentStateChangedEvent) -> None:
        events.changed(ev.new_state, current_trace_id())

    def item_added(ev: ConversationItemAddedEvent) -> None:
        if not isinstance(ev.item, ChatMessage):
            return
        timing = turns.add(ev.item)
        if timing is None:
            return
        log.info("agent turn", extra={"session": p.config.session_id, **timing.log_fields()})
        timing.record(tracer)
        recorder.turn(timing)
        events.emit(EventType.AGENT_TURN_METRICS, timing.payload(), current_trace_id())
        report_usage(final=False)

    def failed(ev: ErrorEvent) -> None:
        stage, vendor = Stage.CONTROL, p.stt
        source = type(ev.source).__module__
        if ".tts" in source:
            stage, vendor = Stage.TTS, p.tts
        elif ".llm" in source:
            stage, vendor = Stage.LLM, p.llm
        elif ".stt" in source:
            stage, vendor = Stage.STT, p.stt
        inner = getattr(ev.error, "error", ev.error)
        err = vendor.classify(inner, stage) if isinstance(inner, BaseException) else None
        log.error(
            "pipeline stage failed",
            extra={
                "session": p.config.session_id,
                "stage": str(stage),
                "code": str(err.code) if err else "internal",
                "native": err.provider.native_code if err and err.provider else None,
                "recoverable": getattr(ev.error, "recoverable", None),
            },
        )

    def closed(ev: CloseEvent) -> None:
        log.info("agent session closed", extra={"session": p.config.session_id, "why": ev.reason})
        usage = report_usage(final=True)
        unpriced = [
            f"{i['stage']}:{i['provider']}/{i['model']}/{i['unit']}"
            for i in usage["items"]
            if not i["priced"]
        ]
        log.info(
            "session cost",
            extra={
                "session": p.config.session_id,
                "cost_inr": usage["costInr"],
                "unpriced": ",".join(unpriced),
            },
        )

    session.on("agent_state_changed", state_changed)
    session.on("conversation_item_added", item_added)
    session.on("error", failed)
    session.on("close", closed)


def new_session(p: Plan, stages: Stages) -> AgentSession[Any]:
    return AgentSession(
        stt=stages.stt,
        llm=stages.llm,
        tts=stages.tts,
        vad=stages.vad,
        turn_handling=p.turn_handling,  # type: ignore[arg-type]
        user_away_timeout=None,
    )


async def entrypoint(ctx: JobContext) -> None:
    redact_framework_logs()
    control = ControlPlane.from_env()
    p = plan(load(ctx.job.metadata), pool(), fetches_keys=control is not None)
    provider = telemetry.install(p.config)
    stages: Stages = build(p)
    room_key = await room_encryption(p, control) if control is not None else None

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY, encryption=room_key)

    async def publish(body: bytes) -> None:
        await ctx.room.local_participant.publish_data(body, reliable=True, topic=TOPIC)

    events = SessionEvents(p.config, publish)
    session = new_session(p, stages)
    watch(session, p, events, telemetry.tracer(provider))

    async def flush() -> None:
        await events.drain()
        if provider is not None:
            provider.force_flush()

    ctx.add_shutdown_callback(flush)
    await session.start(
        agent=Agent(instructions=p.persona.instructions),
        room=ctx.room,
        room_options=room_options(p, stages.tts.sample_rate),
        record=False,
    )
    session.say(p.persona.greeting, allow_interruptions=True)


def prewarm(proc: JobProcess) -> None:
    local_inference.init_vad()


def server() -> AgentServer:
    redact_framework_logs()
    os.environ.setdefault(POOL_ENV, DEFAULT_POOL)
    exposed = exposition()
    agent_server = AgentServer(
        setup_fnc=prewarm,
        prometheus_port=exposed.port if exposed else None,
        prometheus_multiproc_dir=exposed.multiproc_dir if exposed else None,
    )
    agent_server.rtc_session(entrypoint, on_request=on_request)
    return agent_server


__all__ = [
    "entrypoint",
    "new_session",
    "on_request",
    "pool",
    "prewarm",
    "redact_framework_logs",
    "server",
]
