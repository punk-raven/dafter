from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass

from dafter_batch.control import ControlPlane as BatchControl
from dafter_core.config import ResolvedSessionConfig
from dafter_core.enums import EncryptionMode
from dafter_core.errors import DafterError
from dafter_runtime.answering import Roster
from dafter_runtime.control import ControlPlane, encryption
from dafter_runtime.events import TOPIC, SessionEvents
from dafter_runtime.listeners import is_human, is_worker
from dafter_runtime.metrics import exposition
from dafter_runtime.plan import load
from dafter_runtime.toolbox import follow
from dafter_runtime.worker import redact_framework_logs, refuse
from livekit import rtc
from livekit.agents import AgentServer, AutoSubscribe, JobContext, JobRequest
from livekit.agents.worker import WorkerPermissions

from .closing import REQUEST_TOPIC, Closing, command
from .judging import Scorer
from .keeper import MinutesKeeper
from .plan import ScribePlan, plan
from .scoring import scoring_loop
from .scribe import Scribe
from .transcript import Transcript
from .writer import Writer

POOL_ENV = "LIVEKIT_AGENT_NAME"
DEFAULT_POOL = "dafter-scribe"
HTTP_PORT_ENV = "DAFTER_SCRIBE_HTTP_PORT"
LONGEST_AFTER_CALL_S = 3600.0
ROLE = "scribe"
PERMISSIONS = WorkerPermissions(
    can_publish=False,
    can_subscribe=True,
    can_publish_data=True,
    can_update_metadata=False,
    hidden=True,
)

log = logging.getLogger("dafter.scribe")


@dataclass(slots=True)
class Job:
    closing: Closing
    tasks: list[asyncio.Task[None]]
    deadline_s: float

    def stop(self) -> None:
        for task in self.tasks:
            task.cancel()


JOBS: dict[str, Job] = {}


def pool() -> str:
    return os.environ.get(POOL_ENV) or DEFAULT_POOL


async def on_request(req: JobRequest) -> None:
    control = ControlPlane.from_env(ROLE)
    try:
        plan(load(req.job.metadata), pool(), fetches_keys=control is not None)
    except DafterError as exc:
        await refuse(control, req.room.name, exc)
        await req.reject()
        return
    await req.accept(name="Dafter scribe", attributes={"dafter.role": ROLE})


async def room_key(
    cfg: ResolvedSessionConfig, control: ControlPlane | None
) -> rtc.E2EEOptions | None:
    if control is None or cfg.media.encryption.stated_mode is not EncryptionMode.E2EE:
        return None
    try:
        key = await control.session_key(cfg)
    except DafterError as exc:
        await refuse(control, cfg.session_id, exc)
        raise
    return encryption(key)


async def entrypoint(ctx: JobContext) -> None:
    redact_framework_logs()
    control = ControlPlane.from_env(ROLE)
    p: ScribePlan = plan(load(ctx.job.metadata), pool(), fetches_keys=control is not None)
    loop = scoring_loop(p.config)
    scoring_on = loop.sampling.scores_any
    try:
        model = p.writer.build()
        judge = p.judge.build() if p.judge is not None and scoring_on else None
    except DafterError as exc:
        await refuse(control, p.config.session_id, exc)
        raise
    key = await room_key(p.config, control)
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_NONE, encryption=key)

    async def publish(body: bytes) -> None:
        await ctx.room.local_participant.publish_data(body, reliable=True, topic=TOPIC)

    events = SessionEvents(p.config, publish)
    roster = Roster()
    follow(ctx.room, roster)
    writer = Writer(
        model,
        p.writer.vendor.classify,
        events.emit,
        Transcript(roster.label, p.agent_label),
        p.language,
        p.agent_label,
        p.interval_s,
        p.writer.source(),
    )
    scorer = None
    if p.judge is not None and judge is not None:
        writer.spend.watch(judge)
        scorer = Scorer(
            judge,
            p.judge.vendor.classify,
            events.emit,
            p.language,
            p.interval_s,
            p.judge.source(),
            loop,
        )
    closing = Closing(p.config, writer, scorer, events.emit, events.envelope)
    scribe = Scribe(p.config, writer, scorer, closing)

    def received(packet: rtc.DataPacket) -> None:
        if packet.topic == TOPIC:
            scribe.received(packet.data, is_worker(packet.participant))
        elif packet.topic == REQUEST_TOPIC and packet.participant is not None:
            if is_human(packet.participant, ctx.room) and command(packet.data) == "minutes":
                closing.requested()

    def left(participant: rtc.RemoteParticipant) -> None:
        if not roster.present():
            log.info("everyone left, the scribe closes the call's notes")
            ctx.shutdown(reason="everyone left")

    ctx.room.on("data_received", received)
    ctx.room.on("participant_disconnected", left)
    job = Job(closing, [asyncio.ensure_future(writer.run())], p.after_call_s)
    if scorer is not None:
        job.tasks.append(asyncio.ensure_future(scorer.run()))
    JOBS[ctx.job.id] = job

    async def stop(reason: str) -> None:
        job.stop()
        await events.drain()

    ctx.add_shutdown_callback(stop)
    log.info("scribe listening", extra={"session": p.config.session_id})


async def after_call(ctx: JobContext) -> None:
    job = JOBS.pop(ctx.job.id, None)
    if job is None:
        return
    job.stop()
    control = ControlPlane.from_env(ROLE)
    keeper = MinutesKeeper(control.url, control.secret) if control is not None else None
    batch = BatchControl(control.url, control.secret) if control is not None else None
    try:
        await asyncio.wait_for(
            job.closing.after_call(keeper, batch, asyncio.sleep), timeout=job.deadline_s
        )
    except TimeoutError:
        log.warning("after-call work abandoned at the deadline", extra={"job": ctx.job.id})


def server() -> AgentServer:
    redact_framework_logs()
    os.environ.setdefault(POOL_ENV, DEFAULT_POOL)
    exposed = exposition()
    agent_server = AgentServer(
        permissions=PERMISSIONS,
        port=int(os.environ.get(HTTP_PORT_ENV) or 0),
        session_end_timeout=LONGEST_AFTER_CALL_S + 60,
        prometheus_port=exposed.port if exposed else None,
        prometheus_multiproc_dir=exposed.multiproc_dir if exposed else None,
    )
    agent_server.rtc_session(entrypoint, on_request=on_request, on_session_end=after_call)
    return agent_server


__all__ = [
    "JOBS",
    "PERMISSIONS",
    "Job",
    "after_call",
    "entrypoint",
    "on_request",
    "pool",
    "room_key",
    "server",
]
