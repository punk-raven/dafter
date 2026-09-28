from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dafter_core.errors import DafterError
from dafter_runtime.personas import DEFAULT_REF, persona_for

from . import bank as banks
from . import catalog as catalogs
from .judge import Judge
from .report import Row, sample, table
from .run import Screen, Settings, build

FRAMEWORK_LOGGER = "livekit.agents"


def progress(line: str) -> None:
    sys.stderr.write(line + "\n")


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="dafter-screen",
        description=(
            "Text-only LLM screen: every catalog candidate answers a language's question bank "
            "N times; spends provider credits and free-tier quota, so run it deliberately"
        ),
    )
    p.add_argument("--out", type=Path, required=True, help="directory for the result files")
    p.add_argument("--language", default="hi", choices=banks.LANGUAGES)
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--candidates", default=None, help="comma-separated catalog ids; default all")
    p.add_argument("--no-judge", action="store_true", help="measure speed and errors only")
    p.add_argument("--pause", type=float, default=0.0, help="seconds between provider calls")
    p.add_argument("--timeout", type=float, default=20.0, help="seconds per provider call")
    p.add_argument("--seed", type=int, default=0, help="seed for the spot-check sample")
    args = p.parse_args(argv)
    if args.runs < 1:
        p.error("--runs must be at least 1")
    return args


def write(out: Path, rows: list[Row], text: str, seed: int, meta: dict[str, Any]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    records = [r for row in rows for r in row.records]
    with (out / "results.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
    with (out / "spot-check.jsonl").open("w", encoding="utf-8") as f:
        for s in sample(records, seed):
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    summary = {**meta, "ranking": [row.summary() for row in rows]}
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (out / "ranking.md").write_text(text, encoding="utf-8")


def judge_for(catalog: catalogs.Catalog, bank: banks.Bank, timeout: float) -> Judge:
    try:
        model, classify = build(catalog.judge.ref)
    except DafterError as exc:
        raise replace(
            exc,
            message=(
                f"the judge {catalog.judge.id} cannot run: {exc.message}; pass --no-judge "
                "to screen speed and errors without quality scores"
            ),
        ) from exc
    return Judge(model, classify, bank, timeout)


async def screen(args: argparse.Namespace) -> int:
    bank = banks.load(args.language)
    if bank.empty:
        sys.stderr.write(bank.refusal() + "\n")
        return 2
    catalog = catalogs.load()
    picked = catalog.pick(args.candidates.split(",") if args.candidates else None)
    persona = persona_for(DEFAULT_REF, bank.language)
    judge = None if args.no_judge else judge_for(catalog, bank, args.timeout)
    started = datetime.now(UTC)
    settings = Settings(
        date=started.date().isoformat(), runs=args.runs, pause=args.pause, timeout=args.timeout
    )
    try:
        run = Screen(settings, bank, persona.instructions, judge, build=build, say=progress)
        rows = await run.run(picked)
    finally:
        if judge is not None:
            await judge.model.aclose()
    judged_by = f"{catalog.judge.id} ({catalog.judge.ref.model})" if judge else None
    text = table(rows, settings.date, judged_by)
    meta = {
        "date": settings.date,
        "startedAt": started.isoformat(),
        "language": bank.language,
        "runs": args.runs,
        "questions": len(bank.questions),
        "catalogAsOf": catalog.as_of.isoformat(),
        "judge": judged_by,
        "sampleSeed": args.seed,
    }
    write(args.out, rows, text, args.seed, meta)
    sys.stdout.write(text)
    return 0


def main(argv: list[str] | None = None) -> None:
    args = arguments(argv)
    logging.getLogger(FRAMEWORK_LOGGER).setLevel(logging.CRITICAL)
    try:
        code = asyncio.run(screen(args))
    except DafterError as exc:
        sys.stderr.write(f"screen refused: {exc.code}: {exc.message}\n")
        code = 2
    except ValueError as exc:
        sys.stderr.write(f"screen refused: {exc}\n")
        code = 2
    raise SystemExit(code)


if __name__ == "__main__":
    main()
