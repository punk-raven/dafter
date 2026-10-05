from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from dafter_core.config import parse
from livekit.agents import utils

from . import svarah
from .accuracy import estimate, measure, recognizer, setup
from .corpus import Archive, Clip, Sample, fetch_missing, pin_kathbath, verified
from .hearing import hear
from .remote import describe, ranged

TESTDATA = Path(__file__).resolve().parents[4] / "testdata" / "asr"
HF_TOKEN = "HF_TOKEN"


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="dafter-asr",
        description="Word error rate per language on pinned public recordings, through the "
        "STT the worker builds. pin and fetch download clips; run spends provider credits.",
    )
    sub = p.add_subparsers(dest="command", required=True)
    pin = sub.add_parser("pin", help="choose a sample from a source and write its manifest")
    pin.add_argument("dataset", choices=("kathbath", "svarah"))
    pin.add_argument("--language", required=True)
    pin.add_argument("--count", type=int, default=20)
    fetch = sub.add_parser("fetch", help="download a manifest's clips and check their sha256")
    fetch.add_argument("manifests", type=Path, nargs="+")
    run = sub.add_parser("run", help="transcribe a manifest's clips and score them")
    run.add_argument("manifest", type=Path)
    run.add_argument("--job", type=Path, required=True, help="a job vector naming the STT")
    run.add_argument("--mode", default=None, help="override the STT's mode option")
    run.add_argument("--identify", action="store_true", help="identify the language instead")
    run.add_argument("--limit", type=int, default=None, help="only the first N clips")
    run.add_argument("--concurrency", type=int, default=4)
    run.add_argument("--pace", type=float, default=1.0, help="audio speed; 1 is real time")
    run.add_argument("--max-inr", type=Decimal, default=Decimal(25))
    run.add_argument("--out", type=Path, default=None)
    for command in (pin, fetch, run):
        command.add_argument("--testdata", type=Path, default=TESTDATA)
    return p.parse_args(argv)


def sources(testdata: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((testdata / "sources.json").read_text(encoding="utf-8"))
    return loaded


def audio_path(testdata: Path, sample: Sample, clip: Clip) -> Path:
    return testdata / "audio" / sample.dataset / sample.language / f"{clip.id}.wav"


def manifest_path(testdata: Path, dataset: str, language: str) -> Path:
    return testdata / f"{dataset}-{language}.json"


def archive_for(source: dict[str, Any]) -> Archive:
    found = describe(source["archive"])
    if (found.size, found.etag) != (source["bytes"], source["etag"]):
        raise SystemExit(f"{source['archive']} changed since it was pinned: {found}")
    return Archive(found.size, ranged(source["archive"]))


def token() -> str:
    value = os.environ.get(HF_TOKEN)
    if not value:
        raise SystemExit(f"svarah is gated on Hugging Face: set {HF_TOKEN} (see the README)")
    return value


def store(testdata: Path, sample: Sample, audio: dict[str, bytes]) -> None:
    for clip in (c for c in sample.clips if c.id in audio):
        path = audio_path(testdata, sample, clip)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(verified(clip, audio[clip.id]))


def pin(args: argparse.Namespace) -> int:
    source = sources(args.testdata)[args.dataset]
    if args.dataset == "kathbath":
        sample, audio = pin_kathbath(archive_for(source), source, args.language, args.count)
    else:
        sample, audio = svarah.pin_svarah(
            svarah.authorized(token()), source, args.language, args.count
        )
    store(args.testdata, sample, audio)
    path = manifest_path(args.testdata, args.dataset, args.language)
    path.write_text(sample.dumps(), encoding="utf-8")
    print(
        json.dumps({"manifest": str(path), "clips": len(sample.clips), "seconds": sample.seconds})
    )
    return 0


def reader(sample: Sample, source: dict[str, Any]) -> Callable[[Clip], bytes]:
    if sample.dataset == "kathbath":
        archive = archive_for(source)
        return lambda clip: archive.read(clip.member)
    get = svarah.authorized(token())
    return lambda clip: svarah.audio(get, source["dataset"], clip.member)


def fetch_one(testdata: Path, path: Path) -> int:
    sample = Sample.read(path.read_text(encoding="utf-8"))
    read = reader(sample, sources(testdata)[sample.dataset])
    got = fetch_missing(sample, lambda c: audio_path(testdata, sample, c).exists(), read)
    store(testdata, sample, {clip.id: data for clip, data in got})
    return len(got)


def fetch(args: argparse.Namespace) -> int:
    for path in args.manifests:
        print(json.dumps({"manifest": str(path), "fetched": fetch_one(args.testdata, path)}))
    return 0


async def transcribe(args: argparse.Namespace) -> dict[str, Any]:
    sample = Sample.read(args.manifest.read_text(encoding="utf-8"))
    if args.limit is not None:
        sample = Sample(
            sample.dataset,
            sample.language,
            sample.source,
            sample.selection,
            sample.clips[: args.limit],
        )
    cfg = parse(args.job.read_bytes())
    s = setup(cfg, sample, args.mode, args.identify)
    cost = estimate(s, sample)
    if cost is None or cost > args.max_inr:
        raise SystemExit(f"estimated cost {cost} INR is unpriced or over --max-inr {args.max_inr}")
    missing = [c.id for c in sample.clips if not audio_path(args.testdata, sample, c).exists()]
    if missing:
        raise SystemExit(f"{len(missing)} clip(s) not fetched; run dafter-asr fetch first")
    async with utils.http_context.open():
        heard_by = recognizer(s, cfg)

        async def one(clip: Clip) -> Any:
            wav = verified(clip, audio_path(args.testdata, sample, clip).read_bytes())
            return await hear(heard_by, wav, args.pace)

        try:
            return await measure(sample, s, one, args.concurrency)
        finally:
            await heard_by.aclose()


def run(args: argparse.Namespace) -> int:
    report = asyncio.run(transcribe(args))
    report["job"] = {"path": args.job.name, "configHash": parse(args.job.read_bytes()).config_hash}
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    shown = {k: report[k] for k in ("dataset", "language", "stt", "summary")}
    print(json.dumps(shown, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    commands = {"pin": pin, "fetch": fetch, "run": run}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
