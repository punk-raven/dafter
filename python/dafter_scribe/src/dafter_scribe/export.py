from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from importlib import resources
from pathlib import Path
from typing import Any

from dafter_evals import golden
from dafter_evals.screen import bank
from dafter_runtime.personas import base_language

from .review import REVIEW_DIR_ENV, FailedTurn, read_queue

TELEPHONY = "telephony"
REGRESSION_MARK = "r"
SEGMENT_PREFIX = "sg_"
GOLDEN_TAGS = {base_language(tag): tag for tag in golden.LANGUAGES}


def clip_id(turn: FailedTurn) -> str:
    return f"{base_language(turn.language)}-{turn.segment_id.removeprefix(SEGMENT_PREFIX)}"


def golden_candidates(turns: Iterable[FailedTurn], language: str) -> dict[str, Any]:
    tag = GOLDEN_TAGS[base_language(language)]
    clips: list[dict[str, Any]] = []
    cuts: list[dict[str, Any]] = []
    for turn in turns:
        if turn.audio is None:
            continue
        name = clip_id(turn)
        clips.append(
            {
                "id": name,
                "audio": f"{golden.AUDIO_DIRECTORY}/{tag}/{name}.wav",
                "channel": "telephony_8k" if turn.channel == TELEPHONY else "wideband",
                "consentId": turn.audio.consent_id,
                "annotators": [],
                "reference": {"native": turn.question, "romanized": None},
                "entities": [],
                "labels": [],
            }
        )
        cuts.append(
            {
                "clipId": name,
                "sessionId": turn.session_id,
                **turn.audio.to_dict(),
                "failed": list(turn.failed),
                "configVersion": turn.config_version,
            }
        )
    manifest = {
        "version": golden.MANIFEST_VERSION,
        "language": tag,
        "nativeReview": {"status": "pending", "reviewers": []},
        "clips": clips,
    }
    return {"manifest": manifest, "cuts": cuts}


def regression_id(turn: FailedTurn) -> str:
    hexes = turn.segment_id.removeprefix(SEGMENT_PREFIX)
    return f"{base_language(turn.language)}-{REGRESSION_MARK}{hexes}"


def packaged_bank(language: str) -> dict[str, Any]:
    path = resources.files(bank.PACKAGE).joinpath("banks", f"{language}.json")
    document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return document


def bank_with_regressions(
    document: dict[str, Any], turns: Iterable[FailedTurn]
) -> tuple[dict[str, Any], int]:
    questions = list(document["questions"])
    ids = {q["id"] for q in questions}
    texts = {q["text"].strip() for q in questions}
    added = 0
    for turn in turns:
        text = " ".join(turn.question.split())
        question_id = regression_id(turn)
        if not text or text in texts or question_id in ids:
            continue
        questions.append({"id": question_id, "text": text})
        ids.add(question_id)
        texts.add(text)
        added += 1
    merged = {**document, "questions": questions}
    bank.parse(json.dumps(merged))
    return merged, added


def failed_turns(queue: Path, language: str) -> list[FailedTurn]:
    return [t for t in read_queue(queue, language) if t.failed]


def write(document: dict[str, Any], out: Path | None) -> None:
    text = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    if out is None:
        sys.stdout.write(text)
        return
    out.write_text(text, encoding="utf-8")


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dafter-scribe-export",
        description=(
            "Promote production turns the scribe's judge failed, from its review queue "
            f"(${REVIEW_DIR_ENV}), into golden set candidates or screen bank regression cases."
        ),
    )
    sub = p.add_subparsers(dest="target", required=True)
    for target, about in (
        ("golden", "candidate clips in the golden manifest format, plus where to cut each one"),
        ("bank", "the language's screen bank with each failed question added once"),
    ):
        command = sub.add_parser(target, help=about)
        command.add_argument("--queue", type=Path, required=True, help="the review queue folder")
        command.add_argument(
            "--language", required=True, choices=bank.LANGUAGES, help="base language"
        )
        command.add_argument("--out", type=Path, default=None, help="default: standard output")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    turns = failed_turns(args.queue, args.language)
    if args.target == "golden":
        candidates = golden_candidates(turns, args.language)
        write(candidates, args.out)
        skipped = len(turns) - len(candidates["cuts"])
        print(
            f"{len(candidates['cuts'])} candidate clips, {skipped} without consented audio",
            file=sys.stderr,
        )
        return 0
    merged, added = bank_with_regressions(packaged_bank(args.language), turns)
    write(merged, args.out)
    print(f"{added} regression questions added", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
