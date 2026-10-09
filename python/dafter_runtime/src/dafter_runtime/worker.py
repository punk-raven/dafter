from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from typing import Any

from dafter_core.enums import EncryptionMode, EventType, Stage
from dafter_core.errors import DafterError
from dafter_providers import Multilingual, prewarm_turn_detectors
from livekit import local_inference, rtc
from livekit.agents import (
    AgentServer,
    AgentSession,
    AutoSubscribe,
    JobContext,
    JobProcess,
    JobRequest,
    llm,
    stt,
    tts,
)
from livekit.agents.llm import ChatMessage
from livekit.agents.metrics import AgentSessionUsage
from livekit.agents.types import NOT_GIVEN
from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    CloseEvent,
    ConversationItemAddedEvent,
    ErrorEvent,
    SpeechCreatedEvent,
)
from livekit.agents.voice.room_io import (
    AudioOutputOptions,
    RoomOptions,
    TextOutputOptions,
)
from opentelemetry import trace

from . import telemetry
from .answering import Roster
from .backchannel import Acknowledgements
from .called import Called
from .captions import Captions, source_of
from .configured import configured
from .control import ControlPlane, encryption
from .cost import OutputTokens, load_prices, model_name, priced, provider_name, usage_payload
from .delivery import Delivery
from .events import TOPIC, SessionEvents
from .listeners import is_worker
from .metrics import WORKER, SessionMetrics, WorkerMetrics, exposition
from .plan import Plan, load, plan
from .scribing import Scribing
from .speech_plan import SpeechPlan
from .stages import Stages, build, filtered_input, hearing
from .switching import Switching
from .telephony import PhoneLines, Relink, phone_lines
from .timing import Turns, TurnTiming
from .toolbox import Answering, follow, linked, registry_for
from .transcribing import Transcribing

POOL_ENV = "LIVEKIT_AGENT_NAME"
DEFAULT_POOL = "dafter-py"
HTTP_PORT_ENV = "DAFTER_AGENT_HTTP_PORT"

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
        p = plan(load(req.job.metadata), pool(), fetches_keys=control is not None)
    except DafterError as exc:
        await refuse(control, req.room.name, exc)
        await req.reject()
        return
    await req.accept(name=p.config.agent.name or "", attributes={"dafter.role": "agent"})


async def room_encryption(p: Plan, control: ControlPlane) -> rtc.E2EEOptions | None:
    if p.config.media.encryption.stated_mode is not EncryptionMode.E2EE:
        return None
    try:
        key = await control.session_key(p.config)
    except DafterError as exc:
        await refuse(control, p.config.session_id, exc)
        raise
    return encryption(key)


def stt_sample_rate(p: Plan) -> int:
    return int(p.pipeline.stt.options.get("sampleRate", 16000)) if p.pipeline.stt else 16000


def room_options(p: Plan, tts_sample_rate: int, captions: Captions | None = None) -> RoomOptions:
    text_output = TextOutputOptions(next_in_chain=captions.agent) if captions else True
    if p.called_by_name:
        return RoomOptions(
            audio_input=False,
            text_input=False,
            audio_output=AudioOutputOptions(sample_rate=tts_sample_rate),
            text_output=text_output,
            close_on_disconnect=False,
        )
    return RoomOptions(
        audio_input=filtered_input(p, stt_sample_rate(p)),
        audio_output=AudioOutputOptions(sample_rate=tts_sample_rate),
        text_output=text_output,
        close_on_disconnect=not p.takes_phone_calls,
    )


def captions_for(p: Plan, events: SessionEvents) -> Captions | None:
    if not p.config.transcription.live:
        return None
    return Captions(events.emit, source_of(p.pipeline.stt))


def scribing_for(p: Plan, room: rtc.Room, events: SessionEvents, roster: Roster) -> Scribing | None:
    if not p.config.scribe.enabled:
        return None
    scribing = Scribing(p.config.session_id, events.emit, roster.label)

    def received(packet: rtc.DataPacket) -> None:
        if packet.topic == TOPIC:
            scribing.received(packet.data, is_worker(packet.participant))

    room.on("data_received", received)
    return scribing


def current_trace_id() -> str | None:
    ctx = trace.get_current_span().get_span_context()
    return format(ctx.trace_id, "032x") if ctx.is_valid else None


def watch(
    session: AgentSession[Any],
    p: Plan,
    events: SessionEvents,
    tracer: trace.Tracer,
    metrics: WorkerMetrics = WORKER,
    usage: Callable[[], AgentSessionUsage] | None = None,
    filled: Callable[[], bool] = lambda: False,
    after_filler: Callable[[], Mapping[str, float]] = dict,
    language: Callable[[], str | None] = lambda: None,
) -> None:
    turns = Turns(getattr(session.stt, "take_endpoint", None))
    generated = OutputTokens()
    prices = load_prices()
    recorder = SessionMetrics(metrics, p, session)
    spent = usage or (lambda: session.usage)

    def report_usage(final: bool) -> dict[str, Any]:
        items = priced(spent(), prices)
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
        timing = turns.add(ev.item, filled(), language(), after_filler())
        if timing is None:
            return
        report(timing)

    def speech_created(ev: SpeechCreatedEvent) -> None:
        handle = ev.speech_handle
        handle.add_done_callback(lambda _: unheard(handle.scheduled and not handle.chat_items))

    def unheard(cut_off: bool) -> None:
        if cut_off:
            report(turns.unheard())

    def report(timing: TurnTiming) -> None:
        timing.record(tracer)
        recorder.turn(timing)
        events.emit(EventType.AGENT_TURN_METRICS, timing.payload(), current_trace_id())
        tokens = generated.turn(report_usage(final=False))
        fields = {**timing.log_fields(), "output_tokens": tokens}
        log.info("agent turn", extra={"session": p.config.session_id, **fields})

    def failed(ev: ErrorEvent) -> None:
        stage, vendor = Stage.CONTROL, p.stt
        if isinstance(ev.source, tts.TTS):
            stage, vendor = Stage.TTS, p.tts
        elif isinstance(ev.source, llm.LLM):
            stage, vendor = Stage.LLM, p.llm
        elif isinstance(ev.source, stt.STT):
            stage, vendor = Stage.STT, p.stt
        inner = getattr(ev.error, "error", ev.error)
        err = vendor.classify(inner, stage) if isinstance(inner, BaseException) else None
        recoverable = bool(getattr(ev.error, "recoverable", False))
        if err is not None:
            degraded = recorder.degraded(stage, err, recoverable)
            events.emit(EventType.PROVIDER_DEGRADED, degraded, current_trace_id())
        log.error(
            "pipeline stage failed",
            extra={
                "session": p.config.session_id,
                "stage": str(stage),
                "code": str(err.code) if err else "internal",
                "native": err.provider.native_code if err and err.provider else None,
                "recoverable": recoverable,
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
    session.on("speech_created", speech_created)
    session.on("error", failed)
    session.on("close", closed)


def new_session(
    p: Plan, stages: Stages, speech_plan: SpeechPlan | None = None
) -> AgentSession[Any]:
    planned = speech_plan or SpeechPlan(p.config.agent.speech, p.config.language)
    spoken = planned.transforms()
    if p.called_by_name:
        return AgentSession(
            stt=NOT_GIVEN,
            llm=stages.llm,
            tts=stages.tts,
            vad=None,
            turn_handling=p.voice_turn_handling,  # type: ignore[arg-type]
            user_away_timeout=None,
            tts_text_transforms=spoken,
            expressive=p.config.agent.speech.expressive,
        )
    return AgentSession(
        stt=stages.stt,
        llm=stages.llm,
        tts=stages.tts,
        vad=stages.vad,
        turn_handling=hearing(p, stages),  # type: ignore[arg-type]
        user_away_timeout=None,
        tts_text_transforms=spoken,
        expressive=p.config.agent.speech.expressive,
    )


async def built(p: Plan, control: ControlPlane | None) -> Stages:
    try:
        stages = build(p)
    except DafterError as exc:
        await refuse(control, p.config.session_id, exc)
        raise
    log.info("agent stages", extra={"session": p.config.session_id, **served(p, stages)})
    return stages


def flat(effective: dict[str, Any]) -> dict[str, Any]:
    llm = effective["llm"]
    return {**effective, "llm": f"{llm['provider']}/{llm['model']}"}


def served(p: Plan, stages: Stages) -> dict[str, str]:
    return {
        "llm": f"{provider_name(stages.llm.provider)}/{model_name(stages.llm.model)}",
        "llm_route": p.config.llm or "language",
    }


async def entrypoint(ctx: JobContext) -> None:
    redact_framework_logs()
    control = ControlPlane.from_env()
    p = plan(load(ctx.job.metadata), pool(), fetches_keys=control is not None)
    provider = telemetry.install(p.config)
    stages = await built(p, control)
    room_key = await room_encryption(p, control) if control is not None else None

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY, encryption=room_key)

    async def publish(body: bytes) -> None:
        await ctx.room.local_participant.publish_data(body, reliable=True, topic=TOPIC)

    events = SessionEvents(p.config, publish)
    speech_plan = SpeechPlan(p.config.agent.speech, p.config.language)
    session = new_session(p, stages, speech_plan)

    async def flush() -> None:
        await events.drain()
        if provider is not None:
            provider.force_flush()

    ctx.add_shutdown_callback(flush)
    switching = Switching(p.config.agent.language_switching, p.config.language, p.personas)
    captions = captions_for(p, events)
    delivery = Delivery(p.config.agent.speech, p.config.language, p.config.media.audio)
    follow_language(switching, speech_plan, delivery, stages)
    called = (
        Called(ctx, p, stages, session, stt_sample_rate(p), switching, delivery, captions)
        if p.called_by_name
        else None
    )
    transcribing = (
        Transcribing(ctx, p, stages, captions, stt_sample_rate(p))
        if captions is not None and called is None
        else None
    )
    caller: Callable[[], str | None]
    sleep: Callable[[], None] | None = None
    if called is not None:
        caller, roster, sleep = called.addressee, called.roster, called.gate.go_quiet
    else:
        roster = Roster()
        follow(ctx.room, roster)
        caller = linked(session)
    scribing = scribing_for(p, ctx.room, events, roster)
    registry = registry_for(p, session, roster, caller, sleep, delivery, switching, scribing)
    if called is not None:
        called.voice.before_answer = answering(switching, registry.heard)
        called.voice.judging = delivery.filler.hold
        called.voice.announce = events.addressed
    agent = Answering(
        p.persona.instructions,
        registry,
        caller,
        Acknowledgements.of(p.config.turn.interruption.backchannel),
        delivery,
        switching,
        name=p.config.agent.name or "",
    )
    if scribing is not None:
        scribing.briefed = lambda: agent.brief(scribing.context())
    watch(
        session,
        p,
        events,
        telemetry.tracer(provider),
        usage=spent(session, called, transcribing),
        filled=lambda: delivery.filler.took(session.current_speech),
        after_filler=lambda: delivery.filler.reply_layers(session.current_speech),
        language=lambda: switching.language if switching.enabled else None,
    )
    await session.start(
        agent=agent,
        room=ctx.room,
        room_options=room_options(p, stages.tts.sample_rate, captions),
        record=False,
    )
    delivery.start(session, stages.tts)
    effective = configured(
        stages.llm, speech_plan, delivery.filler.enabled, p.config.turn.interruption.backchannel
    )
    events.emit(EventType.AGENT_CONFIGURED, effective, current_trace_id())
    log.info("agent configured", extra={"session": p.config.session_id, **flat(effective)})
    if called is not None:
        called.listen()
    else:
        if p.takes_phone_calls:
            Relink(ctx.room, session.room_io).listen()
        if transcribing is not None:
            transcribing.listen()
    speak_first(p, session, ctx.room)


def speak_first(p: Plan, session: AgentSession[Any], room: rtc.Room) -> PhoneLines | None:
    if not p.on_a_phone and p.opening is not None:
        session.say(p.opening, allow_interruptions=True)
    return phone_lines(p, room, session)


def follow_language(
    switching: Switching, speech_plan: SpeechPlan, delivery: Delivery, stages: Stages
) -> None:
    if not switching.enabled:
        return
    tts = stages.tts
    if isinstance(tts, Multilingual):
        switching.follow_with(tts.speak_in)
    switching.follow_with(speech_plan.speak_in)
    switching.follow_with(lambda language: delivery.filler.speak_in(language, tts))


def answering(
    switching: Switching, heard: Callable[[str, str], None]
) -> Callable[[str, str], None]:
    def before_answer(speaker: str, text: str) -> None:
        switching.answering(speaker)
        heard(speaker, text)

    return before_answer


def spent(
    session: AgentSession[Any], called: Called | None, transcribing: Transcribing | None
) -> Callable[[], AgentSessionUsage] | None:
    if called is not None:
        return called.usage
    if transcribing is None:
        return None
    listening = transcribing
    return lambda: AgentSessionUsage(model_usage=[*session.usage.model_usage, *listening.usage()])


def prewarm(proc: JobProcess) -> None:
    local_inference.init_vad()
    local_inference.init_eot()
    prewarm_turn_detectors()


def server() -> AgentServer:
    redact_framework_logs()
    os.environ.setdefault(POOL_ENV, DEFAULT_POOL)
    exposed = exposition()
    agent_server = AgentServer(
        setup_fnc=prewarm,
        port=int(os.environ.get(HTTP_PORT_ENV) or 0),
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
    "speak_first",
]
