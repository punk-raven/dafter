from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp
import numpy as np
from dafter_core.config import ResolvedSessionConfig, parse
from livekit.agents import utils

from . import interruptions, sweepgate, turntaking, vadscore
from .conditions import (
    CONDITION_KINDS,
    NC_MODES,
    NOISE_DIR_ENV,
    VOICES_DIR_ENV,
    WORK_RATE,
    Audio,
    Condition,
    Sources,
    at_rate,
    conditions_for,
    degrade,
    load_bank,
    nc_overrides,
    read_wav,
    resample,
    source_directory,
)
from .connect import join, session_overrides
from .golden import GoldenClip, load
from .measure import compare
from .probe import SAMPLE_RATE, Echo, Probe
from .scripts import LANGUAGES, Caller, caller_for
from .turns import REPLY_TIMEOUT, SCENARIOS, run
from .voice import Voice

SWEEPS = ("turntaking", "interruptions", "vad")
LIVE_SWEEPS = frozenset({"turntaking", "interruptions"})
ALL_LANGUAGES = "all"
DETAIL_KEY = "trialsDetail"
PASSED = 0
GATE_FAILED = 1
REFUSED = 2


class Refused(SystemExit):
    pass


def overrides(args: argparse.Namespace) -> dict[str, Any]:
    given = json.loads(args.overrides) if args.overrides else {}
    return session_overrides({**given, **nc_overrides(args.nc)}, echoes=args.echo > 0)


def arguments() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="dafter-evals", description="Scripted turns against a live agent"
    )
    p.add_argument("--control", default="http://127.0.0.1:8080")
    p.add_argument("--tenant", default="t_9c21a4be")
    p.add_argument("--language", default="hi", choices=LANGUAGES)
    p.add_argument("--channel", default="webrtc")
    p.add_argument("--turns", type=int, default=20)
    p.add_argument(
        "--speaker", default=None, help="the caller's voice (default: the language's own)"
    )
    p.add_argument("--scenarios", default=",".join(SCENARIOS))
    p.add_argument("--overrides", default=None, help="session overrides, as JSON")
    p.add_argument(
        "--echo",
        type=float,
        default=0.0,
        help="feed the agent's own audio back into the microphone at this gain, as a "
        "speakerphone or phone line without echo cancellation does (0 is off)",
    )
    p.add_argument("--echo-delay-ms", type=int, default=200)
    p.add_argument("--out", type=Path, default=None, help="write the full report here, as JSON")
    p.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="an earlier --out report; print the change on each clock against it. With --sweep "
        "--gate: the sweep thresholds, per language, condition and metric path",
    )
    p.add_argument(
        "--gate",
        action="store_true",
        help="with --sweep: exit 1 when a metric misses its --baseline threshold",
    )
    p.add_argument(
        "--report-only",
        action="store_true",
        help="exit 0 when the go/no-go fails; refusals still exit 2",
    )
    p.add_argument(
        "--sweep",
        default=None,
        help=f"replay golden clips instead of the script: any of {','.join(SWEEPS)}",
    )
    p.add_argument(
        "--conditions",
        default="clean",
        help=f"conditions each sweep runs under: any of {','.join(CONDITION_KINDS)}",
    )
    p.add_argument(
        "--nc", default="off", choices=NC_MODES, help="the one noise filter on the agent's input"
    )
    p.add_argument(
        "--sweep-languages",
        default=None,
        help=f"languages to sweep, comma separated or {ALL_LANGUAGES} (default: --language)",
    )
    p.add_argument("--golden-root", type=Path, default=None, help="golden manifests and audio")
    p.add_argument(
        "--noise-dir", type=Path, default=None, help=f"noise wav files (or ${NOISE_DIR_ENV})"
    )
    p.add_argument(
        "--voices-dir",
        type=Path,
        default=None,
        help=f"background voice wav files (or ${VOICES_DIR_ENV}; default: other golden clips)",
    )
    p.add_argument("--seed", type=int, default=7, help="seeds noise and voice placement")
    return p.parse_args()


async def create_session(args: argparse.Namespace, language: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "tenantId": args.tenant,
        "language": language,
        "channel": args.channel,
    }
    body["overrides"] = overrides(args)
    async with (
        aiohttp.ClientSession() as http,
        http.post(f"{args.control}/sessions", json=body) as resp,
    ):
        created: dict[str, Any] = await resp.json()
        if resp.status != 201:
            raise Refused(f"session refused: {json.dumps(created)}")
        return created


@dataclass
class Live:
    created: dict[str, Any]
    cfg: ResolvedSessionConfig
    probe: Probe
    voice: Voice
    started: float


@asynccontextmanager
async def connected(args: argparse.Namespace, caller: Caller) -> AsyncIterator[Live]:
    created = await create_session(args, caller.language)
    cfg = parse(json.dumps(created["config"]))
    probe = Probe(Echo(args.echo, args.echo_delay_ms))
    async with utils.http_context.open():
        voice = Voice(cfg, caller.speaker)
        try:
            started = time.monotonic()
            await join(probe, args.control, created)
            yield Live(created, cfg, probe, voice, started)
        finally:
            await voice.aclose()
            await probe.close()


def session_record(args: argparse.Namespace, caller: Caller, live: Live) -> dict[str, Any]:
    return {
        "sessionId": live.created["sessionId"],
        "configHash": live.created["configHash"],
        "agentDispatchId": live.created.get("agentDispatchId"),
        "language": caller.language,
        "speaker": caller.speaker,
        "scriptReviewed": caller.script.reviewed,
        "channel": args.channel,
        "overrides": overrides(args),
        "pipeline": route(live.cfg),
    }


async def evaluate(args: argparse.Namespace, caller: Caller) -> dict[str, Any]:
    async with connected(args, caller) as live:
        scenarios = frozenset(args.scenarios.split(","))
        report = await run(
            live.probe, live.voice, caller.script, args.turns, scenarios, live.cfg.budgets
        )
        report["usage"] = usage(live.probe.events.usage, time.monotonic() - live.started)
    report["session"] = session_record(args, caller, live)
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


def sweep_kinds(args: argparse.Namespace) -> frozenset[str]:
    kinds = frozenset(k.strip() for k in args.sweep.split(",") if k.strip())
    unknown = sorted(kinds - set(SWEEPS))
    if unknown or not kinds:
        raise Refused(f"--sweep takes any of {','.join(SWEEPS)}; got {args.sweep!r}")
    return kinds


def sweep_languages(args: argparse.Namespace) -> tuple[str, ...]:
    if not args.sweep_languages:
        return (args.language,)
    if args.sweep_languages == ALL_LANGUAGES:
        return LANGUAGES
    chosen = tuple(lang.strip() for lang in args.sweep_languages.split(",") if lang.strip())
    unknown = [lang for lang in chosen if lang not in LANGUAGES]
    if unknown or not chosen:
        raise Refused(f"--sweep-languages takes {ALL_LANGUAGES} or any of {LANGUAGES}")
    return chosen


def sweep_conditions(args: argparse.Namespace) -> tuple[Condition, ...]:
    try:
        return conditions_for(k.strip() for k in args.conditions.split(",") if k.strip())
    except ValueError as e:
        raise Refused(str(e)) from e


def to_probe(audio: Audio) -> np.ndarray:
    return resample(audio.samples, audio.rate, SAMPLE_RATE)


async def vad_sweep(
    clips: list[GoldenClip], clean: dict[str, Audio], degraded: dict[str, Audio]
) -> dict[str, Any]:
    model = vadscore.detector()
    scored = []
    for clip in clips:
        windows = await vadscore.speech_probabilities(degraded[clip.clip_id], model)
        scored.append(vadscore.scored_clip(clip.clip_id, clean[clip.clip_id], windows))
    return vadscore.summarize(scored)


async def live_sweep(
    args: argparse.Namespace,
    caller: Caller,
    clips: list[GoldenClip],
    degraded: dict[str, Audio],
    kinds: frozenset[str],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    async with connected(args, caller) as live:
        await live.probe.wait_agent_audio(REPLY_TIMEOUT)
        if "turntaking" in kinds:
            turn_trials = [
                await turntaking.replay(live.probe, clip, to_probe(degraded[clip.clip_id]))
                for clip in turntaking.turn_clips(clips)
            ]
            result["turntaking"] = turntaking.summarize(turn_trials)
        if "interruptions" in kinds:
            prompts = caller.script.long_prompts
            overlap_trials = []
            for i, clip in enumerate(interruptions.overlap_clips(clips)):
                prompt = await live.voice.say(prompts[i % len(prompts)])
                pcm = to_probe(degraded[clip.clip_id])
                overlap_trials.append(await interruptions.replay(live.probe, clip, prompt, pcm))
            result["interruptions"] = interruptions.summarize(overlap_trials)
        result["usage"] = usage(live.probe.events.usage, time.monotonic() - live.started)
    result["session"] = session_record(args, caller, live)
    return result


async def sweep_language(
    args: argparse.Namespace,
    language: str,
    conditions: tuple[Condition, ...],
    noises: tuple[Audio, ...],
    kinds: frozenset[str],
) -> dict[str, Any]:
    clips = load(language, args.golden_root)
    if not clips:
        return {"clips": 0}
    clean = {c.clip_id: at_rate(read_wav(c.audio_path, c.clip_id), WORK_RATE) for c in clips}
    voices_dir = source_directory(args.voices_dir, VOICES_DIR_ENV)
    interferers = load_bank(voices_dir) if voices_dir else tuple(clean.values())
    sources = Sources(noises=noises, interferers=interferers)
    rng = np.random.default_rng(args.seed)
    caller = caller_for(language, args.speaker)
    rows: dict[str, Any] = {}
    for condition in conditions:
        degraded = {cid: degrade(condition, audio, sources, rng) for cid, audio in clean.items()}
        row: dict[str, Any] = {}
        if "vad" in kinds:
            row["vad"] = await vad_sweep(clips, clean, degraded)
        if kinds & LIVE_SWEEPS:
            row.update(await live_sweep(args, caller, clips, degraded, kinds))
        rows[condition.name] = row
    return {"clips": len(clips), "conditions": rows}


async def sweep(args: argparse.Namespace) -> dict[str, Any]:
    kinds = sweep_kinds(args)
    conditions = sweep_conditions(args)
    languages = sweep_languages(args)
    noises = load_bank(source_directory(args.noise_dir, NOISE_DIR_ENV))
    if any(c.kind == "snr" for c in conditions) and not noises:
        raise Refused(f"the snr condition needs noise: pass --noise-dir or set {NOISE_DIR_ENV}")
    return {
        "sweep": {
            "kinds": sorted(kinds),
            "conditions": [c.name for c in conditions],
            "nc": args.nc,
            "seed": args.seed,
            "noiseFiles": len(noises),
        },
        "languages": {
            language: await sweep_language(args, language, conditions, noises, kinds)
            for language in languages
        },
    }


def without_detail(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: without_detail(v) for k, v in value.items() if k != DETAIL_KEY}
    if isinstance(value, list):
        return [without_detail(v) for v in value]
    return value


def write(args: argparse.Namespace, report: dict[str, Any], shown: dict[str, Any]) -> None:
    if args.out:
        text = json.dumps(report, ensure_ascii=False, indent=2)
        args.out.write_text(text + "\n", encoding="utf-8")
    sys.stdout.write(json.dumps(shown, ensure_ascii=False, indent=2) + "\n")


def run_sweep(args: argparse.Namespace) -> bool:
    thresholds = None
    if args.gate:
        if args.baseline is None:
            raise Refused("--gate needs --baseline, the sweep thresholds")
        try:
            thresholds = sweepgate.load(args.baseline)
        except ValueError as e:
            raise Refused(str(e)) from e
    report = asyncio.run(sweep(args))
    if thresholds is not None:
        report["gate"] = sweepgate.judge(report, thresholds)
    write(args, report, without_detail(report))
    return thresholds is None or bool(report["gate"]["go"])


def run_script(args: argparse.Namespace) -> bool:
    if args.gate:
        raise Refused("--gate is for --sweep; a scripted run always gates on the session budgets")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else None
    report = asyncio.run(evaluate(args, caller_for(args.language, args.speaker)))
    if baseline is not None:
        report["comparison"] = compare(baseline["summary"], report["summary"])
    write(args, report, {"summary": report["summary"], "comparison": report.get("comparison")})
    return bool(report["summary"]["verdict"]["go"])


def outcome(args: argparse.Namespace) -> int:
    try:
        overrides(args)
    except ValueError as e:
        raise Refused(str(e)) from e
    go = run_sweep(args) if args.sweep else run_script(args)
    return PASSED if go or args.report_only else GATE_FAILED


def main() -> None:
    args = arguments()
    try:
        code = outcome(args)
    except Refused as refused:
        sys.stderr.write(f"evals refused: {refused.code}\n")
        code = REFUSED
    raise SystemExit(code)


if __name__ == "__main__":
    main()
