from __future__ import annotations

import csv
import json
import random
import shutil
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .conditions import LINE_RATE

KEY_KIND = "dafter.tts.listening"
SHEET_NAME = "sheet.csv"
AUDIO_DIRECTORY = "audio"
SIDES = ("first", "second")
VARIANTS = ("a", "b")
SAME = "same"
SCALE = (1.0, 5.0)
SHEET_COLUMNS = (
    "pair",
    "language",
    "spoken",
    "first_audio",
    "second_audio",
    "preferred",
    "first_naturalness",
    "second_naturalness",
    "rater",
    "notes",
)


def label_of(report: Mapping[str, Any]) -> str:
    return str(report["tts"]["id"])


def labels(first: Mapping[str, Any], second: Mapping[str, Any]) -> tuple[str, str]:
    a, b = label_of(first), label_of(second)
    if a != b:
        return a, b
    a, b = f"{a} {first['tts']['voice']}", f"{b} {second['tts']['voice']}"
    if a == b:
        raise ValueError("both runs used the same TTS and voice; nothing to compare")
    return a, b


def audio_by_case(report: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {c["id"]: c for c in report.get("cases", []) if c.get("audio")}


def other(variant: str) -> str:
    return VARIANTS[1] if variant == VARIANTS[0] else VARIANTS[0]


def export(
    first: Mapping[str, Any],
    second: Mapping[str, Any],
    sheet_dir: Path,
    key_path: Path,
    seed: int | None = None,
) -> dict[str, Any]:
    if first.get("language") != second.get("language"):
        raise ValueError("an A/B sheet compares two runs of the same language")
    variant_labels = labels(first, second)
    cases = (audio_by_case(first), audio_by_case(second))
    shared = sorted(set(cases[0]) & set(cases[1]))
    if not shared:
        raise ValueError("the two runs share no case with audio")
    by_variant = dict(zip(VARIANTS, cases, strict=True))
    rng = random.Random(seed)
    rng.shuffle(shared)
    audio_dir = sheet_dir / AUDIO_DIRECTORY
    audio_dir.mkdir(parents=True, exist_ok=True)
    rows, pairs = [], {}
    for n, case_id in enumerate(shared, start=1):
        pair = f"p{n:03d}"
        leading = rng.choice(VARIANTS)
        placed = {}
        order = zip(SIDES, (leading, other(leading)), strict=True)
        for position, (side, variant) in enumerate(order, start=1):
            target = audio_dir / f"{pair}-{position}.wav"
            shutil.copyfile(by_variant[variant][case_id]["audio"], target)
            placed[side] = f"{AUDIO_DIRECTORY}/{target.name}"
        rows.append(
            {
                "pair": pair,
                "language": first["language"],
                "spoken": cases[0][case_id]["spoken"],
                "first_audio": placed["first"],
                "second_audio": placed["second"],
            }
        )
        pairs[pair] = {"case": case_id, "first": leading}
    sheet_path = sheet_dir / SHEET_NAME
    with sheet_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SHEET_COLUMNS, restval="")
        writer.writeheader()
        writer.writerows(rows)
    key = {
        "kind": KEY_KIND,
        "language": first["language"],
        "lineRate": LINE_RATE,
        "variants": dict(zip(VARIANTS, variant_labels, strict=True)),
        "pairs": pairs,
    }
    key_path.write_text(json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"sheet": str(sheet_path), "key": str(key_path), "pairs": len(rows)}


def naturalness(value: str | None, pair: str) -> float | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        score = float(text)
    except ValueError as exc:
        raise ValueError(f"pair {pair}: naturalness {text!r} is not a number") from exc
    if not SCALE[0] <= score <= SCALE[1]:
        low, high = SCALE
        raise ValueError(f"pair {pair}: naturalness {score} is outside {low:g} to {high:g}")
    return score


def tally(rows: Iterable[Mapping[str, str]], key: Mapping[str, Any]) -> dict[str, Any]:
    if key.get("kind") != KEY_KIND:
        raise ValueError("the key is not a dafter-tts listening key")
    scores: dict[str, list[float]] = {v: [] for v in VARIANTS}
    rated: dict[str, set[str]] = {v: set() for v in VARIANTS}
    wins = dict.fromkeys(VARIANTS, 0)
    compared = 0
    raters: set[str] = set()
    for row in rows:
        pair = row.get("pair", "")
        entry = key["pairs"].get(pair)
        if entry is None:
            raise ValueError(f"pair {pair!r} is not in the key")
        order = dict(zip(SIDES, (entry["first"], other(entry["first"])), strict=True))
        for side, variant in order.items():
            score = naturalness(row.get(f"{side}_naturalness"), pair)
            if score is not None:
                scores[variant].append(score)
                rated[variant].add(pair)
        choice = (row.get("preferred") or "").strip().lower()
        if choice in order:
            wins[order[choice]] += 1
        elif choice and choice != SAME:
            raise ValueError(f"pair {pair}: preferred is first, second, same or blank")
        compared += bool(choice)
        rater = (row.get("rater") or "").strip()
        if rater:
            raters.add(rater)
    return {
        key["language"]: {
            key["variants"][v]: variant_rating(scores[v], len(rated[v]), wins[v], compared, raters)
            for v in VARIANTS
        }
    }


def variant_rating(
    scores: Sequence[float], clips: int, wins: int, compared: int, raters: set[str]
) -> dict[str, Any]:
    return {
        "score": round(sum(scores) / len(scores), 2) if scores else None,
        "scale": f"MOS {SCALE[0]:g}-{SCALE[1]:g}",
        "ratings": len(scores),
        "raters": len(raters),
        "clips": clips,
        "preferred": wins,
        "comparisons": compared,
        "lineRate": LINE_RATE,
    }


def tally_files(paths: Iterable[Path], key: Mapping[str, Any]) -> dict[str, Any]:
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open(encoding="utf-8", newline="") as f:
            rows.extend(csv.DictReader(f))
    return tally(rows, key)
