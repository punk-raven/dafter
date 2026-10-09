from __future__ import annotations

import argparse
import asyncio
import difflib
import io
import json
import sys
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
from dafter_core.config import ProviderRef, ResolvedSessionConfig, parse
from dafter_core.enums import Stage, UsageUnit
from dafter_providers import vendor_for
from dafter_runtime.cost import load_prices
from dafter_runtime.personas import base_language
from livekit.agents import tts as lk_tts
from livekit.agents import utils

from . import listening
from .accuracy import DEFAULT_TOLERANCE, Setup, entity_heard, entity_summary, recognizer
from .conditions import LINE_RATE, mulaw_decode, mulaw_encode, resample
from .hearing import TAIL_S, hear
from .wer import character_error_rate, words

KIND = "dafter.tts.roundtrip"
SPEECH_ROOT = Path(__file__).resolve().parents[4] / "testdata" / "speech"
AUDIO_ROOT = SPEECH_ROOT / "audio"
BASELINES = "tts-baselines.json"
PAIR_FILES = {"hi": "hindi", "mr": "marathi", "te": "telugu", "kn": "kannada", "en": "english"}
DEFAULT_VOICE = "priya"
LINE_CODEC = "g711-mulaw"
ENTITY_KIND_OF = {"currency": "amount", "phone": "phone", "date": "date"}
NUMBER_ENTITY = "number"
ASSUMED_CHARS_PER_SECOND = 8


@dataclass(frozen=True, slots=True)
class Pair:
    id: str
    kind: str
    text: str
    spoken: str


@dataclass(frozen=True, slots=True)
class PairSet:
    language: str
    path: Path
    reviewed: bool | None
    pairs: tuple[Pair, ...]


@dataclass(frozen=True, slots=True)
class SpokenEntity:
    kind: str
    text: str
    written: str


@dataclass(frozen=True, slots=True)
class Spoken:
    pair: Pair
    samples: np.ndarray

    @property
    def seconds(self) -> float:
        return self.samples.size / LINE_RATE


def pairs_path(language: str, root: Path = SPEECH_ROOT) -> Path:
    stem = PAIR_FILES.get(base_language(language))
    if stem is None:
        raise ValueError(f"no golden normalization pairs for {language}")
    return root / f"{stem}-normalization.json"


def load_pairs(language: str, root: Path = SPEECH_ROOT) -> PairSet:
    path = pairs_path(language, root)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    reviewed: bool | None = None
    cases = loaded
    if not isinstance(loaded, list):
        cases, reviewed = loaded["cases"], bool(loaded.get("reviewed", False))
    stem = path.name.removesuffix("-normalization.json")
    pairs = tuple(
        Pair(f"{stem}-{n:03d}", c["kind"], c["text"], c["spoken"])
        for n, c in enumerate(cases, start=1)
    )
    return PairSet(language, path, reviewed, pairs)


def entities_of(pair: Pair) -> tuple[SpokenEntity, ...]:
    written, said = words(pair.text), words(pair.spoken)
    matcher = difflib.SequenceMatcher(a=written, b=said, autojunk=False)
    kind = ENTITY_KIND_OF.get(pair.kind, NUMBER_ENTITY)
    return tuple(
        SpokenEntity(kind, " ".join(said[j1:j2]), " ".join(written[i1:i2]))
        for op, i1, i2, j1, j2 in matcher.get_opcodes()
        if op in ("replace", "insert")
    )


def entity_row(entity: SpokenEntity, hypothesis: str) -> dict[str, Any]:
    as_spoken = entity_heard(entity.text, hypothesis)
    as_written = bool(words(entity.written)) and entity_heard(entity.written, hypothesis)
    heard_as = "spoken" if as_spoken else "written" if as_written else None
    return {
        "kind": entity.kind,
        "text": entity.text,
        "written": entity.written,
        "heard": heard_as is not None,
        "heardAs": heard_as,
    }


def case_row(pair: Pair, hypothesis: str, seconds: float, audio: Path | None) -> dict[str, Any]:
    cer = character_error_rate(pair.spoken, hypothesis)
    return {
        "id": pair.id,
        "kind": pair.kind,
        "text": pair.text,
        "spoken": pair.spoken,
        "hypothesis": hypothesis,
        "cer": round(cer.rate, 4),
        "referenceCharacters": cer.reference_length,
        "characterErrors": cer.errors,
        "audioSeconds": round(seconds, 3),
        "audio": str(audio) if audio is not None else None,
        "entities": [entity_row(e, hypothesis) for e in entities_of(pair)],
    }


def ratio(errors: int, length: int) -> float | None:
    return round(errors / length, 4) if length else None


def summary(rows: Sequence[Mapping[str, Any]], failed: int, cost: Decimal) -> dict[str, Any]:
    kinds = sorted({r["kind"] for r in rows})
    by_kind = {
        kind: ratio(
            sum(r["characterErrors"] for r in rows if r["kind"] == kind),
            sum(r["referenceCharacters"] for r in rows if r["kind"] == kind),
        )
        for kind in kinds
    }
    characters = sum(r["referenceCharacters"] for r in rows)
    return {
        "cases": len(rows),
        "failed": failed,
        "cer": ratio(sum(r["characterErrors"] for r in rows), characters),
        "referenceCharacters": characters,
        "cerByKind": by_kind,
        "entities": entity_summary(rows),
        "audioSeconds": round(sum(r["audioSeconds"] for r in rows), 2),
        "costInr": float(cost),
    }


def baseline_key(report: Mapping[str, Any]) -> str:
    voice, heard_by = report["tts"], report["stt"]
    parts = (report["language"], voice["provider"], voice["model"], voice["voice"])
    return "/".join(str(p) for p in (*parts, heard_by["provider"], heard_by["model"]))


def compare(report: Mapping[str, Any], baselines: Mapping[str, Any]) -> dict[str, Any]:
    key = baseline_key(report)
    value = report["summary"]["cer"]
    tolerance = float(baselines.get("tolerance", DEFAULT_TOLERANCE))
    recorded = baselines.get("baselines", {}).get(key)
    found = {"key": key, "metric": "cer", "value": value, "tolerance": tolerance}
    if recorded is None:
        return {**found, "baseline": None, "verdict": "unrecorded"}
    baseline = float(recorded["value"])
    regressed = value is None or value > baseline + tolerance
    return {**found, "baseline": baseline, "verdict": "regressed" if regressed else "held"}


def record(report: Mapping[str, Any], baselines: Mapping[str, Any]) -> dict[str, Any]:
    value = report["summary"]["cer"]
    if value is None:
        raise ValueError("a run with no scored cases cannot be a baseline")
    entry = {
        "metric": "cer",
        "value": value,
        "cases": report["summary"]["cases"],
        "recordedAt": report["ranAt"],
        "configHash": report.get("job", {}).get("configHash"),
    }
    recorded = {**baselines.get("baselines", {}), baseline_key(report): entry}
    return {
        "tolerance": baselines.get("tolerance", DEFAULT_TOLERANCE),
        "baselines": dict(sorted(recorded.items())),
    }


def priced(ref: ProviderRef, unit: UsageUnit, quantity: float) -> Decimal | None:
    price = load_prices().get((ref.provider, ref.model or "", unit))
    return price.cost(quantity) if price is not None else None


def synthesis_cost(ref: ProviderRef, pairs: Sequence[Pair]) -> Decimal | None:
    return priced(ref, UsageUnit.CHARACTER, sum(len(p.spoken) for p in pairs))


def hearing_cost(ref: ProviderRef, seconds: Sequence[float]) -> Decimal | None:
    return priced(ref, UsageUnit.AUDIO_SECOND, sum(s + TAIL_S for s in seconds))


def assumed_seconds(pairs: Sequence[Pair]) -> list[float]:
    return [len(p.spoken) / ASSUMED_CHARS_PER_SECOND for p in pairs]


def within_cap(spent: Sequence[Decimal | None], cap: Decimal, stage: str) -> Decimal:
    if any(s is None for s in spent):
        raise SystemExit(f"the {stage} is unpriced; add it to the price table first")
    total = sum((s for s in spent if s is not None), Decimal(0))
    if total > cap:
        raise SystemExit(f"estimated cost {total} INR for the {stage} is over --max-inr {cap}")
    return total


def voice_ref(cfg: ResolvedSessionConfig, voice: str) -> ProviderRef:
    pipeline = cfg.agent.pipeline
    if pipeline is None or pipeline.tts is None:
        raise SystemExit("the job names no TTS")
    options = {k: v for k, v in pipeline.tts.options.items() if k != "styles"}
    return replace(pipeline.tts, options={**options, "voice": voice})


def hearing_setup(cfg: ResolvedSessionConfig, language: str) -> Setup:
    pipeline = cfg.agent.pipeline
    if pipeline is None or pipeline.stt is None:
        raise SystemExit("the job names no STT")
    return Setup(pipeline.stt, language)


def over_line(samples: np.ndarray, rate: int) -> np.ndarray:
    narrow = resample(samples, rate, LINE_RATE)
    line: np.ndarray = mulaw_decode(mulaw_encode(narrow))
    return line


def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype("<i2").tobytes())
    return buffer.getvalue()


async def speak(voice: lk_tts.TTS[Any], pair: Pair) -> Spoken:
    async with voice.synthesize(pair.spoken) as stream:
        frame = await stream.collect()
    pcm = np.frombuffer(bytes(frame.data), dtype="<i2")
    mono = pcm.reshape(-1, frame.num_channels)[:, 0] if frame.num_channels > 1 else pcm
    return Spoken(pair, over_line(mono, frame.sample_rate))


def failure(pair: Pair, stage: str, exc: BaseException) -> dict[str, Any]:
    return {"id": pair.id, "stage": stage, "error": type(exc).__name__}


async def synthesize_all(
    voice: lk_tts.TTS[Any], pairs: Sequence[Pair], concurrency: int
) -> tuple[list[Spoken], list[dict[str, Any]]]:
    slots = asyncio.Semaphore(concurrency)

    async def one(pair: Pair) -> Spoken | dict[str, Any]:
        async with slots:
            try:
                return await speak(voice, pair)
            except Exception as exc:
                return failure(pair, "tts", exc)

    done = await asyncio.gather(*(one(p) for p in pairs))
    return [s for s in done if isinstance(s, Spoken)], [f for f in done if isinstance(f, dict)]


def store(spoken: Sequence[Spoken], directory: Path) -> dict[str, Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = {}
    for s in spoken:
        path = directory / f"{s.pair.id}.wav"
        path.write_bytes(wav_bytes(s.samples, LINE_RATE))
        paths[s.pair.id] = path
    return paths


async def roundtrip(args: argparse.Namespace) -> dict[str, Any]:
    cfg = parse(args.job.read_bytes())
    found = load_pairs(args.language or cfg.language, args.pairs_root)
    pairs = found.pairs[: args.limit]
    speaking, heard = voice_ref(cfg, args.voice), hearing_setup(cfg, found.language)
    spend = synthesis_cost(speaking, pairs)
    within_cap([spend, hearing_cost(heard.ref, assumed_seconds(pairs))], args.max_inr, "run")
    async with utils.http_context.open():
        build = vendor_for(speaking, Stage.TTS).tts
        if build is None:
            raise SystemExit(f"{speaking.provider} has no TTS")
        voice = build(speaking, found.language)
        try:
            spoken, failures = await synthesize_all(voice, pairs, args.concurrency)
        finally:
            await voice.aclose()
        listened = hearing_cost(heard.ref, [s.seconds for s in spoken])
        total = within_cap([spend, listened], args.max_inr, "ASR pass")
        paths = store(spoken, args.audio_dir / found.language)
        heard_by = recognizer(heard, cfg)
        slots = asyncio.Semaphore(args.concurrency)

        async def one(s: Spoken) -> dict[str, Any]:
            async with slots:
                try:
                    said = await hear(heard_by, wav_bytes(s.samples, LINE_RATE), args.pace)
                except Exception as exc:
                    return failure(s.pair, "stt", exc)
            return case_row(s.pair, said.text, s.seconds, paths[s.pair.id])

        try:
            results = await asyncio.gather(*(one(s) for s in spoken))
        finally:
            await heard_by.aclose()
    rows = [r for r in results if "error" not in r]
    failures += [r for r in results if "error" in r]
    return {
        "kind": KIND,
        "ranAt": datetime.now(UTC).isoformat(timespec="seconds"),
        "language": found.language,
        "pairs": {"file": found.path.name, "reviewed": found.reviewed, "of": len(found.pairs)},
        "tts": {
            "provider": speaking.provider,
            "model": speaking.model,
            "voice": args.voice,
            "id": f"{speaking.provider}/{speaking.model}",
        },
        "stt": heard.to_dict(),
        "line": {"rate": LINE_RATE, "codec": LINE_CODEC},
        "summary": summary(rows, len(failures), total),
        "cases": rows,
        "failures": failures,
    }


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def gate(args: argparse.Namespace, report: dict[str, Any]) -> dict[str, Any]:
    path = args.baseline or args.pairs_root / BASELINES
    baselines = read_json(path)
    verdict = compare(report, baselines)
    if args.record_baseline:
        try:
            write_json(path, record(report, baselines))
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
    return verdict


def run(args: argparse.Namespace) -> int:
    report = asyncio.run(roundtrip(args))
    report["job"] = {"path": args.job.name, "configHash": parse(args.job.read_bytes()).config_hash}
    report["regression"] = gate(args, report)
    if args.out:
        write_json(args.out, report)
    shown = {k: report[k] for k in ("language", "tts", "stt", "summary", "regression")}
    print(json.dumps(shown, ensure_ascii=False))
    return 1 if report["regression"]["verdict"] == "regressed" else 0


def sheet(args: argparse.Namespace) -> int:
    first, second = read_json(args.first), read_json(args.second)
    made = listening.export(first, second, args.sheet_dir, args.key, args.seed)
    print(json.dumps(made, ensure_ascii=False))
    return 0


def tally(args: argparse.Namespace) -> int:
    ratings = listening.tally_files(args.sheets, read_json(args.key))
    if args.out:
        write_json(args.out, ratings)
    print(json.dumps(ratings, ensure_ascii=False))
    return 0


def arguments(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="dafter-tts",
        description="TTS round trip: each golden spoken form is synthesized, sent through an "
        "8 kHz G.711 line, transcribed back and scored by CER and entity accuracy, gated "
        "against per-language baselines. run spends provider credits; sheet and tally do not.",
    )
    sub = p.add_subparsers(dest="command", required=True)
    rt = sub.add_parser("run", help="synthesize, line-code, transcribe and score the pairs")
    rt.add_argument("--job", type=Path, required=True, help="a job vector naming TTS and STT")
    rt.add_argument("--language", default=None, help="defaults to the job's language")
    rt.add_argument("--voice", default=DEFAULT_VOICE)
    rt.add_argument("--audio-dir", type=Path, default=AUDIO_ROOT, help="where the 8 kHz audio goes")
    rt.add_argument("--pairs-root", type=Path, default=SPEECH_ROOT)
    rt.add_argument("--baseline", type=Path, default=None, help="baselines to gate against")
    rt.add_argument("--record-baseline", action="store_true", help="store this run's CER")
    rt.add_argument("--limit", type=int, default=None, help="only the first N pairs")
    rt.add_argument("--concurrency", type=int, default=4)
    rt.add_argument("--pace", type=float, default=1.0, help="audio speed; 1 is real time")
    rt.add_argument("--max-inr", type=Decimal, default=Decimal(25))
    rt.add_argument("--out", type=Path, default=None)
    ab = sub.add_parser("sheet", help="export a blind A/B listening sheet from two run reports")
    ab.add_argument("first", type=Path)
    ab.add_argument("second", type=Path)
    ab.add_argument("--sheet-dir", type=Path, required=True, help="CSV and renamed audio")
    ab.add_argument("--key", type=Path, required=True, help="the unblinding key, kept apart")
    ab.add_argument("--seed", type=int, default=None)
    tl = sub.add_parser("tally", help="fold filled sheets into scorecard naturalness ratings")
    tl.add_argument("sheets", type=Path, nargs="+")
    tl.add_argument("--key", type=Path, required=True)
    tl.add_argument("--out", type=Path, default=None)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = arguments(argv)
    commands = {"run": run, "sheet": sheet, "tally": tally}
    return commands[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
