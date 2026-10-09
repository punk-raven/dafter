from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest
from dafter_evals.listening import KEY_KIND, SHEET_COLUMNS, export, labels, tally, tally_files

CASES = ("hindi-001", "hindi-002", "hindi-003")


def run_report(root: Path, model: str, voice: str, cases: tuple[str, ...] = CASES) -> Any:
    audio = root / f"{model}-{voice}"
    audio.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in cases:
        path = audio / f"{case}.wav"
        path.write_bytes(f"{model} {voice} {case}".encode())
        rows.append({"id": case, "spoken": f"spoken {case}", "audio": str(path)})
    return {
        "language": "hi",
        "tts": {"id": f"sarvam/{model}", "voice": voice},
        "cases": rows,
    }


def test_the_sheet_hides_which_run_is_which(tmp_path: Path) -> None:
    first = run_report(tmp_path, "bulbul:v3", "priya")
    second = run_report(tmp_path, "bulbul:v2", "priya", CASES[:2])
    sheet_dir, key_path = tmp_path / "sheet", tmp_path / "key.json"
    made = export(first, second, sheet_dir, key_path, seed=7)
    assert made["pairs"] == 2
    with (sheet_dir / "sheet.csv").open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        assert tuple(reader.fieldnames or ()) == SHEET_COLUMNS
        rows = list(reader)
    key = json.loads(key_path.read_text(encoding="utf-8"))
    assert key["kind"] == KEY_KIND
    assert key["variants"] == {"a": "sarvam/bulbul:v3", "b": "sarvam/bulbul:v2"}
    by_variant = {"a": first, "b": second}
    for row in rows:
        assert "bulbul" not in row["first_audio"] + row["second_audio"]
        entry = key["pairs"][row["pair"]]
        leading = by_variant[entry["first"]]
        played = (sheet_dir / row["first_audio"]).read_bytes()
        assert (
            played
            == Path(
                next(c["audio"] for c in leading["cases"] if c["id"] == entry["case"])
            ).read_bytes()
        )
        assert row["preferred"] == "" and row["first_naturalness"] == ""
    again = export(first, second, tmp_path / "again", tmp_path / "again.json", seed=7)
    assert again["pairs"] == 2
    assert json.loads((tmp_path / "again.json").read_text())["pairs"] == key["pairs"]


def test_two_runs_of_one_model_are_told_apart_by_voice() -> None:
    first = {"tts": {"id": "sarvam/bulbul:v3", "voice": "priya"}}
    second = {"tts": {"id": "sarvam/bulbul:v3", "voice": "anushka"}}
    assert labels(first, second) == ("sarvam/bulbul:v3 priya", "sarvam/bulbul:v3 anushka")
    with pytest.raises(ValueError, match="same TTS and voice"):
        labels(first, first)


def test_a_sheet_needs_one_language_and_shared_cases(tmp_path: Path) -> None:
    first = run_report(tmp_path, "bulbul:v3", "priya")
    other = {**run_report(tmp_path, "bulbul:v2", "priya"), "language": "te-IN"}
    with pytest.raises(ValueError, match="same language"):
        export(first, other, tmp_path / "s", tmp_path / "k.json")
    disjoint = run_report(tmp_path, "bulbul:v2", "priya", ("hindi-009",))
    with pytest.raises(ValueError, match="share no case"):
        export(first, disjoint, tmp_path / "s", tmp_path / "k.json")


KEY = {
    "kind": KEY_KIND,
    "language": "hi",
    "variants": {"a": "sarvam/bulbul:v3", "b": "sarvam/bulbul:v2"},
    "pairs": {
        "p001": {"case": "hindi-001", "first": "a"},
        "p002": {"case": "hindi-002", "first": "b"},
    },
}


def rated(pair: str, preferred: str, first: str, second: str, rater: str) -> dict[str, str]:
    return {
        "pair": pair,
        "preferred": preferred,
        "first_naturalness": first,
        "second_naturalness": second,
        "rater": rater,
    }


def test_ratings_unblind_into_a_mean_opinion_score_per_variant() -> None:
    rows = [
        rated("p001", "first", "5", "3", "asha"),
        rated("p002", "second", "2", "4", "asha"),
        rated("p001", "same", "4", "4", "ravi"),
        rated("p002", "", "", "", "ravi"),
    ]
    found = tally(rows, KEY)["hi"]
    v3, v2 = found["sarvam/bulbul:v3"], found["sarvam/bulbul:v2"]
    assert (v3["score"], v3["ratings"], v3["clips"], v3["preferred"]) == (4.33, 3, 2, 2)
    assert (v2["score"], v2["ratings"], v2["clips"], v2["preferred"]) == (3.0, 3, 2, 0)
    assert v3["comparisons"] == 3 and v3["raters"] == 2 and v3["lineRate"] == 8000


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (rated("p009", "", "", "", ""), "not in the key"),
        (rated("p001", "", "6", "", ""), "outside 1 to 5"),
        (rated("p001", "", "good", "", ""), "not a number"),
        (rated("p001", "left", "", "", ""), "first, second, same or blank"),
    ],
)
def test_a_badly_filled_sheet_is_refused(row: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        tally([row], KEY)


def test_an_unrated_variant_stays_pending_and_files_are_read(tmp_path: Path) -> None:
    path = tmp_path / "filled.csv"
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=SHEET_COLUMNS, restval="")
        writer.writeheader()
        writer.writerow(rated("p001", "first", "4", "", "asha"))
    found = tally_files([path], KEY)["hi"]
    assert found["sarvam/bulbul:v3"]["score"] == 4.0
    assert found["sarvam/bulbul:v2"]["score"] is None
    with pytest.raises(ValueError, match="not a dafter-tts listening key"):
        tally([], {**KEY, "kind": "other"})
