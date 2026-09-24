from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import aiohttp
from dafter_core.config import parse
from livekit.agents import utils

from .probe import Probe
from .script import HINDI
from .turns import SCENARIOS, run
from .voice import Voice


def arguments() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="dafter-evals", description="Scripted turns against a live agent"
    )
    p.add_argument("--control", default="http://127.0.0.1:8080")
    p.add_argument("--tenant", default="t_9c21a4be")
    p.add_argument("--language", default="hi")
    p.add_argument("--channel", default="webrtc")
    p.add_argument("--turns", type=int, default=20)
    p.add_argument("--speaker", default="ritu")
    p.add_argument("--scenarios", default=",".join(SCENARIOS))
    p.add_argument("--overrides", default=None, help="session overrides, as JSON")
    p.add_argument("--out", type=Path, default=None)
    return p.parse_args()


async def create_session(args: argparse.Namespace) -> dict[str, Any]:
    body: dict[str, Any] = {
        "tenantId": args.tenant,
        "language": args.language,
        "channel": args.channel,
    }
    if args.overrides:
        body["overrides"] = json.loads(args.overrides)
    async with (
        aiohttp.ClientSession() as http,
        http.post(f"{args.control}/sessions", json=body) as resp,
    ):
        created: dict[str, Any] = await resp.json()
        if resp.status != 201:
            raise SystemExit(f"session refused: {json.dumps(created)}")
        return created


async def evaluate(args: argparse.Namespace) -> dict[str, Any]:
    created = await create_session(args)
    cfg = parse(json.dumps(created["config"]))
    probe = Probe()
    async with utils.http_context.open():
        voice = Voice(cfg, args.speaker)
        try:
            await probe.connect(created["url"], created["token"])
            scenarios = frozenset(args.scenarios.split(","))
            report = await run(probe, voice, HINDI, args.turns, scenarios)
        finally:
            await voice.aclose()
            await probe.close()
    report["session"] = {
        "sessionId": created["sessionId"],
        "configHash": created["configHash"],
        "agentDispatchId": created.get("agentDispatchId"),
        "language": args.language,
        "channel": args.channel,
        "overrides": json.loads(args.overrides) if args.overrides else None,
    }
    return report


def main() -> None:
    args = arguments()
    report = asyncio.run(evaluate(args))
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(report["summary"], indent=2) + "\n")


if __name__ == "__main__":
    main()
