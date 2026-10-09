from __future__ import annotations

import argparse
import asyncio
import json
import sys
import wave
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from dafter_core.config import parse
from livekit.agents import utils

from . import golden, loaders
from .accuracy import (
    Expect,
    Expected,
    compare,
    estimate,
    measure,
    public_expected,
    recognizer,
    record,
    setup,
)
from .corpus import Clip, Sample, digest, fetch_missing, verified
from .hearing import hear

TESTDATA = Path(__file__).resolve().parents[4] / "testdata" / "asr"
BASELINES = "baselines.json"
PUBLIC, GOLDEN = "public", "golden"


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="dafter-asr",
        description="WER, CER, orthography-aware WER and entity accuracy per language, on "
        "pinned public recordings or the golden set, through the STT the worker builds, gated "
        "against stored baselines. pin and fetch download clips; run spends provider credits.",
    )
    sub = p.add_subparsers(dest="command", required=True)
    pin = sub.add_parser("pin", help="choose a sample from a source and write its manifest")
    pin.add_argument("dataset", choices=loaders.DATASETS)
    pin.add_argument("--language", required=True)
    pin.add_argument("--count", type=int, default=20)
    fetch = sub.add_parser("fetch", help="download a manifest's clips and check their sha256")
    fetch.add_argument("manifests", type=Path, nargs="+")
    run = sub.add_parser("run", help="transcribe a set's clips and score them")
    run.add_argument("manifest", type=Path, nargs="?", help="a public sample's manifest")
    run.add_argument("--set", dest="clip_set", choices=(PUBLIC, GOLDEN), default=PUBLIC)
    run.add_argument("--language", default=None, help="the golden set's language")
    run.add_argument("--golden-root", type=Path, default=None, help="the golden manifests")
    run.add_argument("--baseline", type=Path, default=None, help="baselines to gate against")
    run.add_argument("--record-baseline", action="store_true", help="store this run's metric")
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
    return audio_root(testdata) / sample.dataset / sample.language / f"{clip.id}.wav"


def manifest_path(testdata: Path, dataset: str, language: str) -> Path:
    return testdata / f"{dataset}-{language}.json"


def audio_root(testdata: Path) -> Path:
    return testdata / "audio"


def store(testdata: Path, sample: Sample, audio: dict[str, bytes]) -> None:
    for clip in (c for c in sample.clips if c.id in audio):
        path = audio_path(testdata, sample, clip)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(verified(clip, audio[clip.id]))


def pin(args: argparse.Namespace) -> int:
    source = sources(args.testdata)[args.dataset]
    sample, audio = loaders.pin_sample(
        args.dataset, source, args.language, args.count, audio_root(args.testdata)
    )
    store(args.testdata, sample, audio)
    path = manifest_path(args.testdata, args.dataset, args.language)
    path.write_text(sample.dumps(), encoding="utf-8")
    print(
        json.dumps({"manifest": str(path), "clips": len(sample.clips), "seconds": sample.seconds})
    )
    return 0


def reader(testdata: Path, sample: Sample, source: dict[str, Any]) -> Callable[[Clip], bytes]:
    return loaders.clip_reader(sample, source, audio_root(testdata))


def fetch_one(testdata: Path, path: Path) -> int:
    sample = Sample.read(path.read_text(encoding="utf-8"))
    read = reader(testdata, sample, sources(testdata)[sample.dataset])
    got = fetch_missing(sample, lambda c: audio_path(testdata, sample, c).exists(), read)
    store(testdata, sample, {clip.id: data for clip, data in got})
    return len(got)


def fetch(args: argparse.Namespace) -> int:
    for path in args.manifests:
        print(json.dumps({"manifest": str(path), "fetched": fetch_one(args.testdata, path)}))
    return 0


@dataclass(frozen=True, slots=True)
class ClipSet:
    sample: Sample
    audio: Callable[[Clip], bytes]
    expect: Expect
    unfetched: int = 0


def limited(sample: Sample, limit: int | None) -> Sample:
    if limit is None:
        return sample
    return Sample(
        sample.dataset, sample.language, sample.source, sample.selection, sample.clips[:limit]
    )


def public_set(args: argparse.Namespace) -> ClipSet:
    if args.manifest is None:
        raise SystemExit("a public run names the manifest of a pinned sample")
    sample = limited(Sample.read(args.manifest.read_text(encoding="utf-8")), args.limit)
    unfetched = sum(1 for c in sample.clips if not audio_path(args.testdata, sample, c).exists())

    def audio(clip: Clip) -> bytes:
        return verified(clip, audio_path(args.testdata, sample, clip).read_bytes())

    return ClipSet(sample, audio, public_expected, unfetched)


def wav_seconds(path: Path) -> float:
    with wave.open(str(path)) as w:
        frames: int = w.getnframes()
        rate: int = w.getframerate()
    return frames / rate


def golden_references(clip: golden.GoldenClip) -> tuple[str, ...]:
    if clip.reference_romanized is None:
        return (clip.reference_native,)
    return (clip.reference_native, clip.reference_romanized)


def golden_set(args: argparse.Namespace) -> ClipSet:
    if args.language is None:
        raise SystemExit("a golden run names its --language")
    found = golden.load(args.language, args.golden_root)[: args.limit]
    if not found:
        raise SystemExit(f"the golden set has no clips for {args.language}")
    missing = [g.clip_id for g in found if not g.audio_path.exists()]
    if missing:
        raise SystemExit(f"{len(missing)} golden clip(s) have no audio on this machine")
    clips = tuple(
        Clip(
            id=g.clip_id,
            member=str(g.audio_path),
            speaker=g.channel,
            duration_s=round(wav_seconds(g.audio_path), 4),
            sha256=digest(g.audio_path.read_bytes()),
            reference=g.reference_native,
        )
        for g in found
    )
    expected = {g.clip_id: Expected(golden_references(g), g.entities) for g in found}
    selection = "every clip of the golden manifest, in manifest order"
    sample = Sample(GOLDEN, args.language, {"set": GOLDEN}, selection, clips)

    def audio(clip: Clip) -> bytes:
        return verified(clip, Path(clip.member).read_bytes())

    return ClipSet(sample, audio, lambda clip: expected[clip.id])


async def transcribe(args: argparse.Namespace) -> dict[str, Any]:
    chosen = golden_set(args) if args.clip_set == GOLDEN else public_set(args)
    sample = chosen.sample
    cfg = parse(args.job.read_bytes())
    s = setup(cfg, sample, args.mode, args.identify)
    cost = estimate(s, sample)
    if cost is None or cost > args.max_inr:
        raise SystemExit(f"estimated cost {cost} INR is unpriced or over --max-inr {args.max_inr}")
    if chosen.unfetched:
        raise SystemExit(f"{chosen.unfetched} clip(s) not fetched; run dafter-asr fetch first")
    async with utils.http_context.open():
        heard_by = recognizer(s, cfg)

        async def one(clip: Clip) -> Any:
            return await hear(heard_by, chosen.audio(clip), args.pace)

        try:
            return await measure(sample, s, one, args.concurrency, chosen.expect)
        finally:
            await heard_by.aclose()


def read_baselines(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def gate(args: argparse.Namespace, report: dict[str, Any]) -> dict[str, Any]:
    path = args.baseline or args.testdata / BASELINES
    baselines = read_baselines(path)
    verdict = compare(report, baselines)
    if args.record_baseline:
        try:
            recorded = record(report, baselines)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
        text = json.dumps(recorded, ensure_ascii=False, indent=2)
        path.write_text(text + "\n", encoding="utf-8")
    return verdict


def run(args: argparse.Namespace) -> int:
    report = asyncio.run(transcribe(args))
    report["job"] = {"path": args.job.name, "configHash": parse(args.job.read_bytes()).config_hash}
    report["regression"] = gate(args, report)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    shown = {k: report[k] for k in ("dataset", "language", "stt", "summary", "regression")}
    print(json.dumps(shown, ensure_ascii=False))
    return 1 if report["regression"]["verdict"] == "regressed" else 0


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    commands = {"pin": pin, "fetch": fetch, "run": run}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
