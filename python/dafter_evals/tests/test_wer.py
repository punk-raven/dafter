from __future__ import annotations

import json
from pathlib import Path

import pytest
from dafter_evals.score import main
from dafter_evals.wer import normalise, score, transcript_text, word_error_rate, words

WER = Path(__file__).resolve().parents[3] / "testdata" / "wer"
REFERENCE = WER / "example-reference.json"
TRANSCRIPT = WER / "example-transcript.json"


@pytest.mark.parametrize(
    ("spoken", "written"),
    [
        ("हाँ", "हां"),
        ("ज़रा", "जरा"),
        ("ज़रा", "जरा"),
        ("बताओ।", "बताओ"),
        ("है॥ ना", "है ना"),
        ("१०", "10"),
        ("क्‍ष", "क्ष"),
        ("Meeting-Room", "meeting room"),
        ("  कल   की  ", "कल की"),
    ],
)
def test_spelling_a_recognizer_may_choose_is_normalised_away(spoken: str, written: str) -> None:
    assert normalise(spoken) == normalise(written)


def test_vowel_signs_and_the_virama_survive_normalisation() -> None:
    assert words("मीटिंग किताब क्या") == ["मीटिंग", "किताब", "क्या"]
    assert normalise("कि") != normalise("की")


def test_the_three_kinds_of_error_are_counted_separately() -> None:
    ref = "a b c d".split()
    assert score(ref, ref).to_dict() == {
        "wer": 0.0,
        "referenceWords": 4,
        "substitutions": 0,
        "deletions": 0,
        "insertions": 0,
    }
    assert score(ref, "a x c d".split()).substitutions == 1
    assert score(ref, "a c d".split()).deletions == 1
    assert score(ref, "a b y c d".split()).insertions == 1
    assert score(ref, []).wer == 1.0
    assert score(["a"], "x y z".split()).wer == 3.0


def test_an_empty_reference_has_no_rate() -> None:
    with pytest.raises(ValueError):
        score([], ["a"])


def test_a_hindi_transcript_is_charged_for_words_not_spelling() -> None:
    reference = json.loads(REFERENCE.read_text())["reference"]
    export = json.loads(TRANSCRIPT.read_text())
    asha = transcript_text(export, "verbatim", "p_4b81e0d7")
    result = word_error_rate(reference, asha)
    assert (result.reference_words, result.deletions, result.substitutions) == (18, 1, 0)
    assert result.insertions == 0
    clean = word_error_rate(reference, transcript_text(export, "clean", "p_4b81e0d7"))
    assert clean.substitutions == 1


def test_every_speaker_is_scored_unless_one_is_named() -> None:
    export = json.loads(TRANSCRIPT.read_text())
    assert "जी ज़रूर" in transcript_text(export)
    assert "जी ज़रूर" not in transcript_text(export, participant="p_4b81e0d7")
    with pytest.raises(ValueError):
        transcript_text({"transcript": {}}, "verbatim")


def test_the_command_prints_the_score(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    code = main(
        [
            "--reference",
            str(REFERENCE),
            "--transcript",
            str(TRANSCRIPT),
            "--participant",
            "p_4b81e0d7",
        ]
    )
    assert code == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == {
        "clip": "example-hi-meeting",
        "language": "hi",
        "rendering": "verbatim",
        "wer": round(1 / 18, 4),
        "referenceWords": 18,
        "substitutions": 0,
        "deletions": 1,
        "insertions": 0,
    }
    text = tmp_path / "heard.txt"
    text.write_text("हाँ मुझे कल की मीटिंग का टाइम बताओ और ज़रा एजेंडा भी भेज देना दस बजे से पहले")
    assert main(["--reference", str(REFERENCE), "--text", str(text)]) == 0
    assert json.loads(capsys.readouterr().out)["wer"] == 0.0
