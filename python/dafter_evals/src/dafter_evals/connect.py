from __future__ import annotations

import sys
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from .probe import Probe

LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost"})
GREETED: dict[str, Any] = {"greets": True}


def session_overrides(given: dict[str, Any] | None) -> dict[str, Any]:
    stated = given or {}
    return {**stated, "agent": {**GREETED, **stated.get("agent", {})}}


def livekit_url(control: str, url: str) -> str:
    host = urlsplit(control).hostname or ""
    if host in LOCAL_HOSTS:
        given = urlsplit(url)
        netloc = f"{host}:{given.port}" if given.port else host
        return urlunsplit(given._replace(netloc=netloc))
    labels = host.split(".")
    labels[0] = "sfu"
    return f"wss://{'.'.join(labels)}"


async def stop_agent(control: str, created: dict[str, Any]) -> None:
    if not created.get("agentDispatchId"):
        return
    endpoint = f"{control}/sessions/{created['sessionId']}/agent/stop"
    try:
        async with aiohttp.ClientSession() as http, http.post(endpoint) as resp:
            if resp.status != 200:
                sys.stderr.write(f"agent not stopped: {resp.status} {await resp.text()}\n")
    except aiohttp.ClientError as exc:
        sys.stderr.write(f"agent not stopped: {type(exc).__name__}\n")


async def join(probe: Probe, control: str, created: dict[str, Any]) -> None:
    try:
        await probe.connect(livekit_url(control, created["url"]), created["token"])
    except BaseException:
        await stop_agent(control, created)
        raise
