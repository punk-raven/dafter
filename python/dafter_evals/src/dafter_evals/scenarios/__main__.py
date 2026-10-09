from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from dafter_core.config import parse
from dafter_core.enums import Stage
from dafter_core.errors import DafterError
from dafter_providers.fallback import FAULT_ENV, injected_faults
from livekit.agents import utils

from ..screen import catalog as catalogs
from ..screen.run import build
from .cost import Ledger, Rates
from .judge import CallJudge
from .report import Gates, summarize, table
from .run import play_suite
from .task import LANGUAGES, SCENARIOS_ROOT, SETS, Task, load, policy
from .trial import Rig
from .voice import Voice, VoiceChain, chain_of

FRAMEWORK_LOGGER = "livekit.agents"
DEFAULT_AGENT_ROLE = "default"
DEFAULT_FALLBACK_ROLE = "baseline"
NONE = "none"


def progress(line: str) -> None:
    sys.stderr.write(line + "\n")


def languages(value: str) -> tuple[str, ...]:
    if value == "all":
        return LANGUAGES
    picked = tuple(dict.fromkeys(v.strip() for v in value.split(",") if v.strip()))
    if not picked or any(v not in LANGUAGES for v in picked):
        raise argparse.ArgumentTypeError(
            f"one or more of {', '.join(LANGUAGES)} separated by commas, or all"
        )
    return picked


def scenario_sets(value: str) -> tuple[str, ...]:
    if value == "all":
        return SETS
    picked = tuple(dict.fromkeys(v.strip() for v in value.split(",") if v.strip()))
    if not picked or any(v not in SETS for v in picked):
        raise argparse.ArgumentTypeError(f"one or more of {', '.join(SETS)}, or all")
    return picked


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="dafter-scenarios",
        description=(
            "tau2-style scenario suite in text mode: each task runs k times against the real "
            "agent persona, tools and consent rules, with a simulated caller and a final-state "
            "check, gated on pass^k. Spends provider credits, capped by --max-inr."
        ),
    )
    p.add_argument("--out", type=Path, required=True, help="directory for the result files")
    p.add_argument("--language", type=languages, default=LANGUAGES, help="comma list or all")
    p.add_argument("--set", type=scenario_sets, default=SETS, help="comma list or all")
    p.add_argument("--task", default=None, help="comma-separated task ids; default every task")
    p.add_argument("--k", type=int, default=4, help="the k in pass^k")
    p.add_argument("--trials", type=int, default=None, help="trials per task; default k")
    p.add_argument(
        "--agent", default=None, help="catalog id of the agent LLM; default role default"
    )
    p.add_argument(
        "--fallback",
        default=None,
        help=f"comma-separated catalog ids of fallback LLMs, or {NONE}; default role baseline",
    )
    p.add_argument("--user", default=None, help="catalog id of the simulated caller; default judge")
    p.add_argument("--no-judge", action="store_true", help="final-state checks only")
    p.add_argument("--job", type=Path, default=None, help="job vector naming the TTS chain")
    p.add_argument("--root", type=Path, default=SCENARIOS_ROOT, help="the scenario files")
    p.add_argument("--timeout", type=float, default=20.0, help="seconds per provider call")
    p.add_argument("--turn-timeout", type=float, default=90.0, help="seconds per agent turn")
    p.add_argument("--max-inr", type=Decimal, default=Decimal(50))
    p.add_argument("--min-pass-k", type=float, default=0.85)
    p.add_argument("--min-tool-success", type=float, default=0.99)
    p.add_argument("--min-entity-accuracy", type=float, default=0.98)
    args = p.parse_args(argv)
    if args.k < 1:
        p.error("--k must be at least 1")
    if args.trials is None:
        args.trials = args.k
    if args.trials < args.k:
        p.error("--trials must be at least --k")
    return args


def selected(args: argparse.Namespace) -> list[Task]:
    tasks = [t for language in args.language for t in load(language, args.root, args.set)]
    if args.task is None:
        return tasks
    wanted = [i.strip() for i in args.task.split(",") if i.strip()]
    known = {t.id for t in tasks}
    missing = [i for i in wanted if i not in known]
    if missing:
        raise ValueError(f"no such task in the chosen languages: {', '.join(missing)}")
    return [t for t in tasks if t.id in wanted]


def by_role(catalog: catalogs.Catalog, role: str) -> catalogs.Candidate:
    found = next((c for c in catalog.candidates if c.role == role), None)
    if found is None:
        raise ValueError(f"the catalog has no {role} candidate")
    return found


def candidates(
    args: argparse.Namespace, catalog: catalogs.Catalog
) -> tuple[catalogs.Candidate, tuple[catalogs.Candidate, ...], catalogs.Candidate]:
    every = {c.id: c for c in (catalog.judge, *catalog.candidates)}

    def named(ident: str) -> catalogs.Candidate:
        if ident not in every:
            raise ValueError(f"not in the catalog: {ident}")
        return every[ident]

    agent = named(args.agent) if args.agent else by_role(catalog, DEFAULT_AGENT_ROLE)
    if args.fallback == NONE:
        fallbacks: tuple[catalogs.Candidate, ...] = ()
    elif args.fallback:
        fallbacks = tuple(named(i.strip()) for i in args.fallback.split(",") if i.strip())
    else:
        fallbacks = tuple(c for c in (by_role(catalog, DEFAULT_FALLBACK_ROLE),) if c.id != agent.id)
    user = named(args.user) if args.user else catalog.judge
    return agent, fallbacks, user


def voice_maker(job: Path | None) -> tuple[VoiceChain | None, Callable[[Task, bool], Voice] | None]:
    if job is None:
        return None, None
    chain = chain_of(parse(job.read_bytes()))
    return chain, lambda task, faulted: Voice(chain, task.language, faulted)


def estimated(rates: Rates, task: Task, faults: frozenset[Stage], voices: bool) -> Decimal:
    return rates.trial_estimate(task, voices and Stage.TTS in faults, Stage.LLM in faults)


def write(out: Path, records: list[dict[str, Any]], summary: dict[str, Any], text: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "results.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (out / "report.md").write_text(text, encoding="utf-8")


async def scenarios(args: argparse.Namespace) -> int:
    env_faults = injected_faults()
    if Stage.TTS in env_faults and args.job is None:
        raise ValueError(f"{FAULT_ENV} names tts: pass --job naming a TTS and its fallback")
    tasks = selected(args)
    catalog = catalogs.load()
    agent, fallbacks, user = candidates(args, catalog)
    judging = None if args.no_judge else catalog.judge
    chain, make_voice = voice_maker(args.job)
    rates = Rates.of((agent, *fallbacks), user, judging, chain)
    estimate = (
        sum(
            (estimated(rates, t, t.faults | env_faults, make_voice is not None) for t in tasks),
            Decimal(0),
        )
        * args.trials
    )
    if estimate > args.max_inr:
        raise ValueError(f"estimated cost {estimate:.2f} INR is over --max-inr {args.max_inr}")
    started = datetime.now(UTC)
    user_model = build(user.ref)
    judge_model = build(catalog.judge.ref) if judging is not None else None
    rig = Rig(
        make_llms=lambda: [build(agent.ref), *(build(f.ref) for f in fallbacks)],
        user=user_model,
        policy=policy(args.root),
        timeout=args.timeout,
        turn_timeout=args.turn_timeout,
        make_voice=make_voice,
        faults=env_faults,
    )
    judge = CallJudge(*judge_model, args.timeout) if judge_model is not None else None
    ledger = Ledger(args.max_inr)
    try:
        async with utils.http_context.open():
            played = await play_suite(tasks, rig, judge, rates, ledger, args.trials, progress)
    finally:
        await user_model[0].aclose()
        if judge_model is not None:
            await judge_model[0].aclose()
    gates = Gates(args.k, args.min_pass_k, args.min_tool_success, args.min_entity_accuracy)
    summary = {
        "date": started.date().isoformat(),
        "startedAt": started.isoformat(),
        "mode": "text",
        "k": args.k,
        "trialsPerTask": args.trials,
        "agent": f"{agent.id} ({agent.ref.model})",
        "fallbacks": [f"{f.id} ({f.ref.model})" for f in fallbacks],
        "user": f"{user.id} ({user.ref.model})",
        "judge": f"{catalog.judge.id} ({catalog.judge.ref.model})" if judging else None,
        "faultsFromEnvironment": sorted(str(f) for f in env_faults),
        "estimatedInr": str(estimate),
        "spentInr": str(ledger.spent),
        "maxInr": str(args.max_inr),
        **summarize(played, gates),
    }
    text = table(summary, args.k)
    write(args.out, played.records, summary, text)
    sys.stdout.write(text)
    return 0 if summary["gate"]["pass"] else 1


def main(argv: list[str] | None = None) -> None:
    args = arguments(argv)
    logging.getLogger(FRAMEWORK_LOGGER).setLevel(logging.CRITICAL)
    try:
        code = asyncio.run(scenarios(args))
    except DafterError as exc:
        sys.stderr.write(f"scenarios refused: {exc.code}: {exc.message}\n")
        code = 2
    except ValueError as exc:
        sys.stderr.write(f"scenarios refused: {exc}\n")
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
