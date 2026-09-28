from __future__ import annotations

import asyncio
import logging
import os

from dafter_core.config import ResolvedSessionConfig
from dafter_core.enums import EncryptionMode
from dafter_core.errors import DafterError
from dafter_runtime.answering import Roster
from dafter_runtime.control import ControlPlane, encryption
from dafter_runtime.events import TOPIC, SessionEvents
from dafter_runtime.plan import load
from dafter_runtime.toolbox import follow
from dafter_runtime.worker import redact_framework_logs, refuse
from livekit import rtc
from livekit.agents import AgentServer, AutoSubscribe, JobContext, JobRequest
from livekit.agents.worker import WorkerPermissions

from .plan import ScribePlan, plan
from .scribe import Scribe, from_worker
from .transcript import Transcript
from .writer import Writer

POOL_ENV = "LIVEKIT_AGENT_NAME"
DEFAULT_POOL = "dafter-scribe"
HTTP_PORT_ENV = "DAFTER_SCRIBE_HTTP_PORT"
ROLE = "scribe"
PERMISSIONS = WorkerPermissions(
    can_publish=False,
    can_subscribe=True,
    can_publish_data=True,
    can_update_metadata=False,
    hidden=True,
)

log = logging.getLogger("dafter.scribe")


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
    try:
        model = p.writer.build()
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
    scribe = Scribe(p.config, writer)

    def received(packet: rtc.DataPacket) -> None:
        if packet.topic == TOPIC:
            scribe.received(packet.data, from_worker(packet.participant))

    def left(participant: rtc.RemoteParticipant) -> None:
        if not roster.present():
            log.info("everyone left, the scribe closes the call's notes")
            ctx.shutdown(reason="everyone left")

    ctx.room.on("data_received", received)
    ctx.room.on("participant_disconnected", left)
    running = asyncio.ensure_future(writer.run())

    async def stop(reason: str) -> None:
        running.cancel()
        await events.drain()

    ctx.add_shutdown_callback(stop)
    log.info("scribe listening", extra={"session": p.config.session_id})


def server() -> AgentServer:
    redact_framework_logs()
    os.environ.setdefault(POOL_ENV, DEFAULT_POOL)
    agent_server = AgentServer(
        permissions=PERMISSIONS, port=int(os.environ.get(HTTP_PORT_ENV) or 0)
    )
    agent_server.rtc_session(entrypoint, on_request=on_request)
    return agent_server


__all__ = ["PERMISSIONS", "entrypoint", "on_request", "pool", "room_key", "server"]
