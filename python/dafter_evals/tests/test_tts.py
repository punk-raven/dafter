from __future__ import annotations

import io
import wave
from decimal import Decimal
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from dafter_core.config import ProviderRef, parse
from dafter_evals.conditions import LINE_RATE
from dafter_evals.tts import (
    AUDIO_ROOT,
    PAIR_FILES,
    SPEECH_ROOT,
    Pair,
    arguments,
    case_row,
    compare,
    entities_of,
    load_pairs,
    main,
    over_line,
    record,
    summary,
    synthesis_cost,
    voice_ref,
    wav_bytes,
    within_cap,
)

ROOT = Path(__file__).resolve().parents[3]
JOB = ROOT / "testdata" / "agent" / "hindi-webrtc-job.json"
RUPEES = Pair(
    "hindi-001",
    "currency",
    "कुल ₹1,25,000 लगेंगे।",
    "कुल एक लाख पच्चीस हज़ार रुपये लगेंगे।",
)


def report(cer: float | None, cases: int = 3) -> dict[str, Any]:
    return {
        "ranAt": "2026-10-08T10:00:00+00:00",
        "language": "hi",
        "tts": {"provider": "sarvam", "model": "bulbul:v3", "voice": "priya"},
        "stt": {"provider": "sarvam", "model": "saaras:v3-realtime"},
        "summary": {"cer": cer, "cases": cases},
        "job": {"configHash": "0" * 64},
    }


@pytest.mark.parametrize("language", ["hi", "mr-IN", "te-IN", "kn-IN", "en-IN"])
def test_every_focus_language_has_golden_pairs(language: str) -> None:
    found = load_pairs(language)
    assert found.pairs
    assert all(p.spoken.strip() and p.text.strip() for p in found.pairs)
    assert len({p.id for p in found.pairs}) == len(found.pairs)
    assert (found.reviewed is None) == (language == "hi")
    assert found.path.name.startswith(PAIR_FILES[language[:2]])


def test_a_language_without_pairs_is_refused() -> None:
    with pytest.raises(ValueError, match="no golden normalization pairs"):
        load_pairs("ta-IN")


def test_the_spoken_amount_is_the_entity_and_the_written_form_is_its_alternate() -> None:
    (entity,) = entities_of(RUPEES)
    assert entity.kind == "amount"
    assert entity.text == "एक लाख पच्चीस हजार रुपये"
    assert entity.written == "1 25 000"


def test_an_untouched_sentence_has_no_entity() -> None:
    assert entities_of(Pair("hindi-040", "untouched", "COVID-19 है।", "COVID-19 है।")) == ()


def test_a_perfect_round_trip_scores_zero_and_hears_the_amount() -> None:
    row = case_row(RUPEES, RUPEES.spoken, 2.5, Path("hindi-001.wav"))
    assert row["cer"] == 0.0
    assert row["entities"] == [
        {
            "kind": "amount",
            "text": "एक लाख पच्चीस हजार रुपये",
            "written": "1 25 000",
            "heard": True,
            "heardAs": "spoken",
        }
    ]


def test_digits_heard_back_still_count_the_amount_but_cost_characters() -> None:
    row = case_row(RUPEES, "कुल 1,25,000 लगेंगे", 2.5, None)
    assert row["cer"] > 0
    assert row["entities"][0]["heardAs"] == "written"
    garbled = case_row(RUPEES, "कुल एक लाख लगेंगे", 2.5, None)
    assert garbled["entities"][0]["heard"] is False


def test_the_summary_pools_characters_per_kind_and_entities() -> None:
    rows = [
        case_row(RUPEES, RUPEES.spoken, 2.0, None),
        case_row(RUPEES, "कुल एक लाख लगेंगे", 1.5, None),
    ]
    found = summary(rows, failed=1, cost=Decimal("0.12"))
    errors = sum(r["characterErrors"] for r in rows)
    characters = sum(r["referenceCharacters"] for r in rows)
    assert found["cer"] == round(errors / characters, 4)
    assert found["cerByKind"] == {"currency": found["cer"]}
    assert found["entities"]["heard"] == 1 and found["entities"]["of"] == 2
    assert (found["failed"], found["audioSeconds"], found["costInr"]) == (1, 3.5, 0.12)


def test_the_gate_holds_within_tolerance_and_fails_when_cer_rises() -> None:
    assert compare(report(0.05), {})["verdict"] == "unrecorded"
    baselines = record(report(0.05), {})
    (key,) = baselines["baselines"]
    assert key == "hi/sarvam/bulbul:v3/priya/sarvam/saaras:v3-realtime"
    assert compare(report(0.06), baselines)["verdict"] == "held"
    assert compare(report(0.08), baselines)["verdict"] == "regressed"
    assert compare(report(None), baselines)["verdict"] == "regressed"
    with pytest.raises(ValueError, match="no scored cases"):
        record(report(None), baselines)


def test_the_line_round_trip_is_8_khz_pcm() -> None:
    tone = (8000 * np.sin(np.linspace(0, 400 * np.pi, 24000))).astype(np.int16)
    line = over_line(tone, 24000)
    assert line.dtype == np.int16 and line.size == 8000
    with wave.open(io.BytesIO(wav_bytes(line, LINE_RATE))) as w:
        assert (w.getframerate(), w.getnchannels(), w.getnframes()) == (LINE_RATE, 1, 8000)


def test_the_voice_is_the_jobs_tts_with_the_chosen_speaker_and_no_styles() -> None:
    ref = voice_ref(parse(JOB.read_bytes()), "priya")
    assert (ref.provider, ref.model, ref.options["voice"]) == ("sarvam", "bulbul:v3", "priya")
    assert "styles" not in ref.options


def test_synthesis_is_priced_per_character_and_capped() -> None:
    ref = ProviderRef("sarvam", "bulbul:v3")
    assert synthesis_cost(ref, [RUPEES]) == Decimal("3.00") * len(RUPEES.spoken) / 1000
    assert synthesis_cost(ProviderRef("nobody", "x"), [RUPEES]) is None
    assert within_cap([Decimal("1"), Decimal("2")], Decimal("5"), "run") == Decimal("3")
    with pytest.raises(SystemExit, match="unpriced"):
        within_cap([None], Decimal("5"), "run")
    with pytest.raises(SystemExit, match="over --max-inr"):
        within_cap([Decimal("6")], Decimal("5"), "run")


def test_a_run_over_its_cap_stops_before_any_provider_call(tmp_path: Path) -> None:
    argv = ["run", "--job", str(JOB), "--audio-dir", str(tmp_path), "--max-inr", "0"]
    with pytest.raises(SystemExit, match="over --max-inr"):
        main(argv)
    assert not any(tmp_path.iterdir())


def test_run_audio_lands_in_the_ignored_speech_audio_directory() -> None:
    args = arguments(["run", "--job", str(JOB)])
    assert args.audio_dir == AUDIO_ROOT == SPEECH_ROOT / "audio"
    assert args.audio_dir == ROOT / "testdata" / "speech" / "audio"
