from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dafter_core.enums import UsageUnit
from dafter_runtime.cost import load_prices

from .accuracy import KIND as ACCURACY
from .measure import CALLER_SPANS, LAYERS

KIND = "dafter.scorecard"
PENDING_ACCURACY = "no public-recording run for this STT and language"
PENDING_LIVE_ACCURACY = "the live accuracy set with human speakers is not recorded yet"
PENDING_LATENCY = "no live dafter-evals run for this stack and language"
PENDING_NATURALNESS = "no human naturalness rating yet"
PENDING_COST = "no live run, so only the STT's price per audio minute is known"

Stack = tuple[str, str | None, str | None]


def offline_row(report: Mapping[str, Any]) -> dict[str, Any]:
    s, stt = report["summary"], report["stt"]
    return {
        "dataset": report["dataset"],
        "mode": stt.get("mode"),
        "hearing": stt["hearing"],
        "wer": s["wer"],
        "clips": s["clips"],
        "failed": s["failed"],
        "referenceWords": s["referenceWords"],
        "latinWords": s.get("latinWords"),
        "identified": (
            None
            if s.get("identifiedOf") in (None, 0)
            else round(s["identifiedCorrectly"] / s["identifiedOf"], 4)
        ),
        "finalAfterAudioMsP50": s["finalAfterAudioMs"]["p50"],
        "ranAt": report["ranAt"],
    }


def live_row(report: Mapping[str, Any]) -> dict[str, Any]:
    summary = report["summary"]
    caller = summary["caller"]
    layers = summary["worker"]["layers"]
    return {
        "sessionId": report["session"]["sessionId"],
        "configHash": report["session"]["configHash"],
        "turns": summary["turns"],
        "caller": {span: {q: caller[span][q] for q in ("p50", "p95")} for span in CALLER_SPANS},
        "worker": {layer: layers[layer]["p50"] for layer in LAYERS},
        "bargeInStopP50Ms": summary["barge_in"]["state_stop_p50_ms"],
        "go": summary["verdict"]["go"],
        "costPerMinuteInr": report.get("usage", {}).get("costPerMinuteInr"),
        "unpriced": report.get("usage", {}).get("unpriced", []),
    }


def stt_price_per_minute(stt: str) -> float | None:
    provider, _, model = stt.partition("/")
    price = load_prices().get((provider, model, UsageUnit.AUDIO_SECOND))
    return float(price.cost(60)) if price is not None else None


def naturalness_of(ratings: Mapping[str, Any], language: str, stack: Stack) -> dict[str, Any]:
    rated = ratings.get(language, {}).get(" ".join(s or "-" for s in stack))
    if rated is None:
        rated = ratings.get(language, {}).get(stack[2] or "")
    return rated if rated is not None else {"score": None, "pending": PENDING_NATURALNESS}


def card(
    language: str,
    stack: Stack,
    offline: list[dict[str, Any]],
    live: list[dict[str, Any]],
    ratings: Mapping[str, Any],
) -> dict[str, Any]:
    pending = []
    if not offline:
        pending.append(PENDING_ACCURACY)
    pending.append(PENDING_LIVE_ACCURACY)
    if not live:
        pending.extend([PENDING_LATENCY, PENDING_COST])
    naturalness = naturalness_of(ratings, language, stack)
    if naturalness.get("score") is None:
        pending.append(PENDING_NATURALNESS)
    costs = [r["costPerMinuteInr"] for r in live if r["costPerMinuteInr"] is not None]
    return {
        "stt": stack[0],
        "llm": stack[1],
        "tts": stack[2],
        "errorRate": {"offline": offline, "live": None},
        "latency": live or None,
        "naturalness": naturalness,
        "costPerMinuteInr": {
            "sttPerAudioMinute": stt_price_per_minute(stack[0]),
            "perCallMinute": round(sum(costs) / len(costs), 4) if costs else None,
        },
        "pending": pending,
    }


def stacks(
    accuracy: Iterable[Mapping[str, Any]], live: Iterable[Mapping[str, Any]]
) -> dict[str, Any]:
    offline: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for report in accuracy:
        stt = f"{report['stt']['provider']}/{report['stt']['model']}"
        offline.setdefault((report["language"], stt), []).append(offline_row(report))
    runs: dict[tuple[str, Stack], list[dict[str, Any]]] = {}
    for report in live:
        route = report["session"]["pipeline"]
        stack = (route["stt"], route.get("llm"), route.get("tts"))
        runs.setdefault((report["session"]["language"], stack), []).append(live_row(report))
    keys = {(language, stack) for language, stack in runs}
    covered = {(language, stack[0]) for language, stack in keys}
    keys |= {
        (language, (stt, None, None)) for language, stt in offline if (language, stt) not in covered
    }
    return {
        "keys": sorted(keys, key=lambda k: (k[0], [s or "" for s in k[1]])),
        "offline": offline,
        "runs": runs,
    }


def scorecard(
    accuracy: list[Mapping[str, Any]],
    live: list[Mapping[str, Any]],
    ratings: Mapping[str, Any],
    languages: Iterable[str],
) -> dict[str, Any]:
    for report in accuracy:
        if report.get("kind") != ACCURACY:
            raise ValueError("an --accuracy file is not a dafter-asr report")
    found = stacks(accuracy, live)
    cards: dict[str, list[dict[str, Any]]] = {language: [] for language in languages}
    for language, stack in found["keys"]:
        rows = found["offline"].get((language, stack[0]), [])
        runs = found["runs"].get((language, stack), [])
        cards.setdefault(language, []).append(card(language, stack, rows, runs, ratings))
    return {
        "kind": KIND,
        "generatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "languages": {language: {"stacks": c} for language, c in cards.items()},
    }


def cell(value: Any, fmt: str = "{}") -> str:
    return "pending" if value is None else fmt.format(value)


HEADER = (
    "| STT / LLM / TTS | Error rate (offline) | Caller gap p50/p95 | Naturalness "
    "| Cost per minute |\n|---|---|---|---|---|"
)
NOTHING = "| pending: no run for this language | pending | pending | pending | pending |"


def price_cell(cost: Mapping[str, Any]) -> str:
    stt = cell(cost["sttPerAudioMinute"], "{:.2f}")
    call = cell(cost["perCallMinute"], "{:.2f}")
    return f"STT Rs {stt}/audio min, call Rs {call}/min"


def markdown(card_set: Mapping[str, Any]) -> str:
    lines = []
    for language, entry in card_set["languages"].items():
        lines += [f"## {language}", "", HEADER]
        if not entry["stacks"]:
            lines.append(NOTHING)
        for c in entry["stacks"]:
            route = " / ".join(s or "-" for s in (c["stt"], c["llm"], c["tts"]))
            wer = (
                "; ".join(
                    f"{r['dataset']} {r['mode'] or ''} {r['hearing']}: {r['wer']:.1%}"
                    + (f", {r['identified']:.0%} identified" if r["identified"] is not None else "")
                    for r in c["errorRate"]["offline"]
                )
                or "pending"
            )
            gaps = [
                f"{r['caller']['gap_ms']['p50']}/{r['caller']['gap_ms']['p95']} ms"
                for r in c["latency"] or []
            ]
            price = price_cell(c["costPerMinuteInr"])
            natural = cell(c["naturalness"].get("score"))
            lines.append(
                f"| {route} | {wer} | {', '.join(gaps) or 'pending'} | {natural} | {price} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip("\n")


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="dafter-scorecard",
        description="One scorecard per language: error rate, latency split, naturalness and "
        "cost per minute for every stack measured. Offline: it reads reports, calls nothing.",
    )
    p.add_argument("--accuracy", type=Path, nargs="*", default=[], help="dafter-asr reports")
    p.add_argument("--live", type=Path, nargs="*", default=[], help="dafter-evals --out reports")
    p.add_argument("--naturalness", type=Path, default=None, help="human ratings, see README")
    p.add_argument("--languages", default="hi,en-IN,kn-IN,mr-IN,te-IN")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--markdown", type=Path, default=None)
    return p.parse_args(argv)


def read(paths: list[Path]) -> list[Mapping[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in paths]


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    ratings = read([args.naturalness])[0] if args.naturalness else {}
    built = scorecard(read(args.accuracy), read(args.live), ratings, args.languages.split(","))
    if args.out:
        args.out.write_text(
            json.dumps(built, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    table = markdown(built)
    if args.markdown:
        args.markdown.write_text(table + "\n", encoding="utf-8")
    sys.stdout.write(table + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
