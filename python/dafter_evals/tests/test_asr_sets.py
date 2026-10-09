from __future__ import annotations

import asyncio
import io
import json
import wave
from pathlib import Path
from typing import Any

import pytest
from dafter_core.config import parse
from dafter_evals.accuracy import Expected, compare, measure, record, setup
from dafter_evals.asr import BASELINES, arguments, gate, golden_set, main
from dafter_evals.corpus import Clip, Sample
from dafter_evals.golden import GoldenEntity
from dafter_evals.hearing import Heard

ROOT = Path(__file__).resolve().parents[3]
JOB = ROOT / "testdata" / "agent" / "kannada-webrtc-job.json"
MANIFEST = ROOT / "testdata" / "asr" / "kathbath-kn-IN.json"
NATIVE = "मेरा नंबर 98765 43210 है"
ROMANIZED = "mera number 98765 43210 hai"


def silence(seconds: float, rate: int = 8000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(rate * seconds))
    return out.getvalue()


def golden_root(tmp_path: Path) -> Path:
    (tmp_path / "audio").mkdir()
    (tmp_path / "audio" / "call-1.wav").write_bytes(silence(1.0))
    clip = {
        "id": "call-1",
        "channel": "telephony_8k",
        "audio": "audio/call-1.wav",
        "reference": {"native": NATIVE, "romanized": ROMANIZED},
        "entities": [{"kind": "phone", "text": "98765 43210", "startChar": 10, "endChar": 21}],
        "labels": [],
        "annotators": ["annotator-a", "annotator-b"],
        "consentId": "consent-1",
    }
    manifest = {
        "version": 1,
        "language": "hi",
        "nativeReview": {"status": "pending", "reviewers": []},
        "clips": [clip],
    }
    (tmp_path / "hi.json").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    return tmp_path


def golden_command(root: Path, *extra: str) -> list[str]:
    return ["run", "--set", "golden", "--golden-root", str(root), "--job", str(JOB), *extra]


def golden_arguments(root: Path, *extra: str) -> Any:
    return arguments(golden_command(root, *extra))


def test_the_golden_set_is_read_with_both_spellings_and_its_entities(tmp_path: Path) -> None:
    root = golden_root(tmp_path)
    chosen = golden_set(golden_arguments(root, "--language", "hi"))
    (clip,) = chosen.sample.clips
    assert (chosen.sample.dataset, chosen.sample.language) == ("golden", "hi")
    assert (clip.id, clip.reference, clip.duration_s) == ("call-1", NATIVE, 1.0)
    expected = chosen.expect(clip)
    assert expected.references == (NATIVE, ROMANIZED)
    assert [(e.kind, e.text) for e in expected.entities] == [("phone", "98765 43210")]
    assert chosen.audio(clip) == silence(1.0)


def test_a_golden_run_needs_a_language_with_clips_and_audio(tmp_path: Path) -> None:
    root = golden_root(tmp_path)
    with pytest.raises(SystemExit, match="--language"):
        golden_set(golden_arguments(root))
    with pytest.raises(SystemExit, match="no clips"):
        golden_set(golden_arguments(root, "--language", "te-IN"))
    (root / "audio" / "call-1.wav").unlink()
    with pytest.raises(SystemExit, match="no audio"):
        golden_set(golden_arguments(root, "--language", "hi"))


def test_a_golden_run_over_its_budget_never_starts(tmp_path: Path) -> None:
    root = golden_root(tmp_path)
    with pytest.raises(SystemExit, match="max-inr"):
        main(golden_command(root, "--language", "hi", "--max-inr", "0.000001"))


def test_a_public_run_needs_its_manifest() -> None:
    with pytest.raises(SystemExit, match="manifest"):
        main(["run", "--job", str(JOB)])


def test_a_run_reports_every_rate_its_primary_metric_and_entities() -> None:
    whole = Sample.read(MANIFEST.read_text(encoding="utf-8"))
    first, second = whole.clips[:2]
    sample = Sample(whole.dataset, whole.language, whole.source, whole.selection, (first, second))
    entities = {
        first.id: (GoldenEntity("name", first.reference, 0, len(first.reference)),),
        second.id: (GoldenEntity("address", second.reference, 0, len(second.reference)),),
    }
    heard = {first.id: first.reference, second.id: second.reference.split(" ", 1)[1]}

    async def fake(clip: Clip) -> Heard:
        return Heard(heard[clip.id], ("kn-IN",), (0.9,), 300)

    def expect(clip: Clip) -> Expected:
        return Expected((clip.reference, "romanized alternate"), entities[clip.id])

    setting = setup(parse(JOB.read_bytes()), sample, None, identify=False)
    report = asyncio.run(measure(sample, setting, fake, concurrency=2, expect=expect))
    summary = report["summary"]
    assert summary["primary"] == {"metric": "cer", "value": summary["cer"]}
    assert 0 < summary["cer"] < 1 and 0 < summary["wer"] < 1
    assert summary["oiwer"] <= summary["wer"]
    assert summary["entities"] == {
        "heard": 1,
        "of": 2,
        "accuracy": 0.5,
        "byKind": {"name": {"heard": 1, "of": 1}, "address": {"heard": 0, "of": 1}},
    }
    assert report["clips"][0]["alternateReferences"] == ["romanized alternate"]
    assert report["clips"][0]["entities"] == [
        {"kind": "name", "text": first.reference, "heard": True}
    ]


def report_with(value: float | None, metric: str = "cer") -> dict[str, Any]:
    return {
        "dataset": "kathbath",
        "language": "kn-IN",
        "stt": {"provider": "sarvam", "model": "saaras", "mode": "codemix", "hearing": "kn-IN"},
        "ranAt": "2026-10-08T00:00:00+00:00",
        "summary": {"primary": {"metric": metric, "value": value}, "clips": 20},
        "job": {"configHash": "hash"},
    }


def test_a_language_regresses_only_beyond_its_own_baseline_and_tolerance() -> None:
    assert compare(report_with(0.1), {})["verdict"] == "unrecorded"
    stored = record(report_with(0.1), {"tolerance": 0.02})
    entry = stored["baselines"]["kathbath/kn-IN/sarvam/saaras/codemix/kn-IN"]
    assert entry == {
        "metric": "cer",
        "value": 0.1,
        "clips": 20,
        "recordedAt": "2026-10-08T00:00:00+00:00",
        "configHash": "hash",
    }
    assert compare(report_with(0.115), stored)["verdict"] == "held"
    regressed = compare(report_with(0.13), stored)
    assert (regressed["verdict"], regressed["baseline"]) == ("regressed", 0.1)
    assert compare(report_with(None), stored)["verdict"] == "regressed"
    assert compare(report_with(0.5, "wer"), stored)["verdict"] == "unrecorded"
    with pytest.raises(ValueError):
        record(report_with(None), stored)


def test_a_recorded_baseline_gates_the_next_run(tmp_path: Path) -> None:
    common = ["run", str(MANIFEST), "--job", str(JOB), "--testdata", str(tmp_path)]
    first = gate(arguments([*common, "--record-baseline"]), report_with(0.1))
    assert first["verdict"] == "unrecorded"
    stored = json.loads((tmp_path / BASELINES).read_text(encoding="utf-8"))
    assert stored["tolerance"] == 0.02 and len(stored["baselines"]) == 1
    assert gate(arguments(common), report_with(0.2))["verdict"] == "regressed"


def test_the_committed_baselines_are_well_formed() -> None:
    stored = json.loads((MANIFEST.parent / BASELINES).read_text(encoding="utf-8"))
    assert 0 < stored["tolerance"] < 1
    assert all(e["metric"] in ("wer", "cer") for e in stored["baselines"].values())
