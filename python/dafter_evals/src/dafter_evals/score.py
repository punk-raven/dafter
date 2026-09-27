from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .wer import transcript_text, word_error_rate


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="dafter-wer",
        description=(
            "Word error rate of a transcript against the verified reference of a known clip, "
            "after Indic normalisation. Offline: it calls no provider."
        ),
    )
    p.add_argument("--reference", type=Path, required=True, help="the clip's reference JSON")
    heard = p.add_mutually_exclusive_group(required=True)
    heard.add_argument(
        "--transcript", type=Path, help="a transcript version export (GET .../transcripts/{v})"
    )
    heard.add_argument("--text", type=Path, help="a plain text transcript")
    p.add_argument("--rendering", choices=("verbatim", "clean"), default="verbatim")
    p.add_argument("--participant", default=None, help="score only this speaker's lines")
    return p.parse_args(argv)


def hypothesis(args: argparse.Namespace) -> str:
    if args.text is not None:
        text: str = args.text.read_text(encoding="utf-8")
        return text
    export: dict[str, Any] = json.loads(args.transcript.read_text(encoding="utf-8"))
    return transcript_text(export, args.rendering, args.participant)


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    reference: dict[str, Any] = json.loads(args.reference.read_text(encoding="utf-8"))
    try:
        result = word_error_rate(reference["reference"], hypothesis(args))
    except (KeyError, ValueError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    report = {"clip": reference.get("clip"), "language": reference.get("language")}
    if args.transcript is not None:
        report["rendering"] = args.rendering
    print(json.dumps({**report, **result.to_dict()}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
