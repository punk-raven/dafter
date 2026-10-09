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
from dafter_runtime.personas import DEFAULT_REF, Persona, persona_for
from livekit.agents import llm

from . import bank as banks
from . import catalog as catalogs
from .judge import Judge
from .report import Row, gate, merged, sample, table
from .run import Screen, Settings, build
from .tools import TOOLS
from .turn import Classify

FRAMEWORK_LOGGER = "livekit.agents"
PASSED = 0
GATE_FAILED = 1
REFUSED = 2
INCOMPLETE = REFUSED


def progress(line: str) -> None:
    sys.stderr.write(line + "\n")


def languages(value: str) -> tuple[str, ...]:
    if value == "all":
        return banks.LANGUAGES
    picked = tuple(dict.fromkeys(v.strip() for v in value.split(",") if v.strip()))
    unknown = [v for v in picked if v not in banks.LANGUAGES]
    if not picked or unknown:
        raise argparse.ArgumentTypeError(
            f"one or more of {', '.join(banks.LANGUAGES)} separated by commas, or all"
        )
    return picked


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="dafter-screen",
        description=(
            "Text-only LLM screen: every catalog candidate answers a language's question bank "
            "N times; spends provider credits and free-tier quota, so run it deliberately"
        ),
    )
    p.add_argument("--out", type=Path, required=True, help="directory for the result files")
    p.add_argument(
        "--language",
        type=languages,
        default=("hi",),
        help=f"comma-separated, from {', '.join(banks.LANGUAGES)}, or all; default hi",
    )
    p.add_argument("--runs", type=int, default=5)
    p.add_argument("--candidates", default=None, help="comma-separated catalog ids; default all")
    p.add_argument("--no-judge", action="store_true", help="measure speed and errors only")
    p.add_argument("--pause", type=float, default=0.0, help="seconds between provider calls")
    p.add_argument("--timeout", type=float, default=20.0, help="seconds per provider call")
    p.add_argument("--seed", type=int, default=0, help="seed for the spot-check sample")
    p.add_argument(
        "--retries", type=int, default=3, help="retries per call on a rate limit or transport fault"
    )
    p.add_argument(
        "--backoff", type=float, default=2.0, help="seconds before the first retry, doubling"
    )
    p.add_argument(
        "--report-only",
        action="store_true",
        help="exit 0 even when a candidate misses or the run is incomplete; refusals still exit 2",
    )
    args = p.parse_args(argv)
    if args.runs < 1:
        p.error("--runs must be at least 1")
    if args.retries < 0 or args.backoff < 0:
        p.error("--retries and --backoff must not be negative")
    return args


def write(
    out: Path,
    rows: list[Row],
    by_language: dict[str, list[Row]],
    text: str,
    seed: int,
    meta: dict[str, Any],
) -> None:
    out.mkdir(parents=True, exist_ok=True)
    records = [r for row in rows for r in row.records]
    with (out / "results.jsonl").open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
    with (out / "spot-check.jsonl").open("w", encoding="utf-8") as f:
        for s in sample(records, seed, judged=meta["judge"] is not None):
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    summary = {
        **meta,
        "ranking": [row.summary() for row in rows],
        "byLanguage": {lang: [r.summary() for r in found] for lang, found in by_language.items()},
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (out / "ranking.md").write_text(text, encoding="utf-8")


def judge_model(catalog: catalogs.Catalog) -> tuple[llm.LLM[Any], Classify]:
    try:
        return build(catalog.judge.ref)
    except DafterError as exc:
        raise replace(
            exc,
            message=(
                f"the judge {catalog.judge.id} cannot run: {exc.message}; pass --no-judge "
                "to screen speed and errors without quality scores"
            ),
        ) from exc


def runnable(
    picked: tuple[str, ...],
) -> tuple[list[tuple[banks.Bank, Persona]], dict[str, str]]:
    ready: list[tuple[banks.Bank, Persona]] = []
    skipped: dict[str, str] = {}
    for language in picked:
        bank = banks.load(language)
        if bank.empty:
            skipped[language] = bank.refusal()
            continue
        try:
            ready.append((bank, persona_for(DEFAULT_REF, language, None)))
        except DafterError as exc:
            skipped[language] = f"no {bank.name} persona: {exc.message}"
    return ready, skipped


def report(
    rows: list[Row],
    by_language: dict[str, list[Row]],
    skipped: dict[str, str],
    date: str,
    judge: str | None,
    rate: catalogs.Rate,
) -> str:
    parts = [table(rows, date, judge, list(by_language))]
    if len(by_language) > 1:
        parts += [table(found, date, judge, [lang]) for lang, found in by_language.items()]
    parts.append(f"Costs are at paid prices in INR; USD prices converted at {rate.stated()}.\n")
    if skipped:
        parts.append("".join(f"Skipped {lang}: {why}\n" for lang, why in skipped.items()))
    return "\n".join(parts)


async def screen(args: argparse.Namespace) -> int:
    ready, skipped = runnable(args.language)
    for reason in skipped.values():
        sys.stderr.write(f"skipped: {reason}\n")
    if not ready:
        return REFUSED
    catalog = catalogs.load()
    picked = catalog.pick(args.candidates.split(",") if args.candidates else None)
    judging = None if args.no_judge else judge_model(catalog)
    started = datetime.now(UTC)
    settings = Settings(
        date=started.date().isoformat(),
        runs=args.runs,
        pause=args.pause,
        timeout=args.timeout,
        retries=args.retries,
        backoff=args.backoff,
    )
    by_language: dict[str, list[Row]] = {}
    try:
        for bank, persona in ready:
            judge = Judge(judging[0], judging[1], bank, args.timeout) if judging else None
            run = Screen(
                settings,
                bank,
                persona.instructions,
                judge,
                build=build,
                say=progress,
                tools=list(TOOLS),
            )
            by_language[bank.language] = await run.run(picked)
    finally:
        if judging is not None:
            await judging[0].aclose()
    rows = merged(list(by_language.values()))
    judged_by = f"{catalog.judge.id} ({catalog.judge.ref.model})" if judging else None
    text = report(rows, by_language, skipped, settings.date, judged_by, catalog.usd_to_inr)
    verdict = gate(rows)
    meta = {
        "date": settings.date,
        "startedAt": started.isoformat(),
        "languages": list(by_language),
        "skippedLanguages": skipped,
        "runs": args.runs,
        "questions": {bank.language: len(bank.questions) for bank, _ in ready},
        "catalogAsOf": catalog.as_of.isoformat(),
        "usdToInr": {
            "inrPerUsd": float(catalog.usd_to_inr.inr_per_usd),
            "asOf": catalog.usd_to_inr.as_of.isoformat(),
            "source": catalog.usd_to_inr.source,
        },
        "judge": judged_by,
        "sampleSeed": args.seed,
        "gate": verdict,
    }
    write(args.out, rows, by_language, text, args.seed, meta)
    sys.stdout.write(text)
    for kind in ("misses", "incomplete"):
        for candidate, found in verdict[kind].items():
            sys.stderr.write(f"{kind}: {candidate}: {'; '.join(found)}\n")
    return exit_code(verdict, args.report_only)


def exit_code(verdict: dict[str, Any], report_only: bool) -> int:
    if verdict["go"] or report_only:
        return PASSED
    return GATE_FAILED if verdict["misses"] else INCOMPLETE


def main(argv: list[str] | None = None) -> None:
    args = arguments(argv)
    logging.getLogger(FRAMEWORK_LOGGER).setLevel(logging.CRITICAL)
    try:
        code = asyncio.run(screen(args))
    except DafterError as exc:
        sys.stderr.write(f"screen refused: {exc.code}: {exc.message}\n")
        code = REFUSED
    except ValueError as exc:
        sys.stderr.write(f"screen refused: {exc}\n")
        code = REFUSED
    raise SystemExit(code)


if __name__ == "__main__":
    main()
