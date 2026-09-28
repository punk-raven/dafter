from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

import aiohttp
from dafter_core.config import ResolvedSessionConfig, parse
from livekit.agents import utils

from .connect import join, session_overrides
from .measure import compare
from .probe import Probe
from .script import HINDI
from .turns import SCENARIOS, run
from .voice import Voice


def overrides(args: argparse.Namespace) -> dict[str, Any]:
    return session_overrides(json.loads(args.overrides) if args.overrides else None)


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
    p.add_argument("--out", type=Path, default=None, help="write the full report here, as JSON")
    p.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="an earlier --out report; print the change on each clock against it",
    )
    return p.parse_args()


async def create_session(args: argparse.Namespace) -> dict[str, Any]:
    body: dict[str, Any] = {
        "tenantId": args.tenant,
        "language": args.language,
        "channel": args.channel,
    }
    body["overrides"] = overrides(args)
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
            started = time.monotonic()
            await join(probe, args.control, created)
            scenarios = frozenset(args.scenarios.split(","))
            report = await run(probe, voice, HINDI, args.turns, scenarios, cfg.budgets)
            report["usage"] = usage(probe.events.usage, time.monotonic() - started)
        finally:
            await voice.aclose()
            await probe.close()
    report["session"] = {
        "sessionId": created["sessionId"],
        "configHash": created["configHash"],
        "agentDispatchId": created.get("agentDispatchId"),
        "language": args.language,
        "channel": args.channel,
        "overrides": overrides(args),
        "pipeline": route(cfg),
    }
    return report


def route(cfg: ResolvedSessionConfig) -> dict[str, str | None]:
    pipeline = cfg.agent.pipeline
    refs = {"stt": pipeline.stt, "llm": pipeline.llm, "tts": pipeline.tts} if pipeline else {}
    return {stage: f"{r.provider}/{r.model}" if r else None for stage, r in refs.items()}


def usage(payload: dict[str, Any] | None, seconds: float) -> dict[str, Any]:
    cost = payload.get("costInr") if payload else None
    items = payload.get("items", []) if payload else []
    return {
        "callSeconds": round(seconds, 1),
        "costInr": cost,
        "costPerMinuteInr": round(cost / (seconds / 60), 4) if cost is not None else None,
        "unpriced": [
            f"{i['stage']}:{i['provider']}/{i['model']}" for i in items if not i["priced"]
        ],
    }


def main() -> None:
    args = arguments()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    report = asyncio.run(evaluate(args))
    if baseline is not None:
        report["comparison"] = compare(baseline["summary"], report["summary"])
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    shown = {"summary": report["summary"], "comparison": report.get("comparison")}
    sys.stdout.write(json.dumps(shown, indent=2) + "\n")


if __name__ == "__main__":
    main()
