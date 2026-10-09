from __future__ import annotations

import json
from pathlib import Path

import pytest
from dafter_evals import golden
from dafter_evals.screen import bank
from dafter_scribe.export import bank_with_regressions, golden_candidates, main, packaged_bank
from dafter_scribe.review import AudioReference, FailedTurn, ReviewQueue, queue_path, read_queue

AUDIO = AudioReference(
    consent_id="consent_rec",
    participant_ids=("p_4b81e0d7",),
    segment_ids=("sg_00000000000000a0",),
    heard_from="2026-10-08T10:00:00+00:00",
    heard_to="2026-10-08T10:00:03+00:00",
)


def failed(
    segment: str,
    question: str,
    audio: AudioReference | None = AUDIO,
    channel: str = "telephony",
    language: str = "hi",
) -> FailedTurn:
    return FailedTurn(
        session_id="s_7f3a9c21",
        language=language,
        channel=channel,
        segment_id=segment,
        question=question,
        reply="मुंबई पास है।",
        criteria={"correctness": "fail", "language": "pass"},
        score=0.5,
        reasoning="wrong distance",
        judged_at="2026-10-08T10:00:05+00:00",
        config_version={"id": "support-v4", "arm": "candidate"},
        audio=audio,
    )


def test_a_kept_turn_reads_back_unchanged(tmp_path: Path) -> None:
    turn = failed("sg_00000000000000a1", "दिल्ली से मुंबई कितनी दूर?")
    queue = ReviewQueue(queue_path(tmp_path, "hi", "s_7f3a9c21"))
    assert queue.keep(turn) and queue.keep(failed("sg_00000000000000b1", "और?", audio=None))
    assert list(read_queue(tmp_path, "hi")) == [turn, failed("sg_00000000000000b1", "और?", None)]
    assert list(read_queue(tmp_path, "te")) == []


def test_a_malformed_queue_line_names_where_it_is(tmp_path: Path) -> None:
    path = queue_path(tmp_path, "hi", "s_7f3a9c21")
    path.parent.mkdir(parents=True)
    path.write_text('{"version": 2}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"s_7f3a9c21\.jsonl:1"):
        list(read_queue(tmp_path, "hi"))


def test_golden_candidates_take_only_turns_with_consented_audio(tmp_path: Path) -> None:
    turns = [
        failed("sg_00000000000000a1", "दिल्ली से मुंबई कितनी दूर?"),
        failed("sg_00000000000000b1", "और?", audio=None),
    ]
    out = golden_candidates(turns, "hi")
    [clip] = out["manifest"]["clips"]
    assert clip["id"] == "hi-00000000000000a1"
    assert clip["audio"] == "audio/hi/hi-00000000000000a1.wav"
    assert clip["channel"] == "telephony_8k" and clip["consentId"] == "consent_rec"
    assert clip["reference"] == {"native": "दिल्ली से मुंबई कितनी दूर?", "romanized": None}
    [cut] = out["cuts"]
    assert cut["clipId"] == clip["id"] and cut["participantIds"] == ["p_4b81e0d7"]
    assert cut["failed"] == ["correctness"]
    labelled = {**clip, "annotators": ["a_1", "a_2"]}
    manifest = {**out["manifest"], "clips": [labelled]}
    [parsed] = golden.parse_manifest(manifest, "hi", tmp_path)
    assert parsed.consent_id == "consent_rec"


def test_golden_candidates_use_the_golden_set_language_tag() -> None:
    out = golden_candidates([failed("sg_00000000000000c1", "ಹೇಗಿದ್ದೀರಿ?", language="kn-IN")], "kn")
    assert out["manifest"]["language"] == "kn-IN"
    assert out["manifest"]["clips"][0]["audio"].startswith("audio/kn-IN/")


def test_a_wideband_call_is_tagged_wideband() -> None:
    out = golden_candidates([failed("sg_00000000000000a1", "हाँ?", channel="webrtc")], "hi")
    assert out["manifest"]["clips"][0]["channel"] == "wideband"


def test_bank_regressions_are_added_once_and_stay_a_valid_bank() -> None:
    document = packaged_bank("hi")
    existing = document["questions"][0]["text"]
    turns = [
        failed("sg_00000000000000a1", "दिल्ली से\nमुंबई कितनी दूर?"),
        failed("sg_00000000000000a2", "दिल्ली से मुंबई कितनी दूर?"),
        failed("sg_00000000000000a3", existing),
    ]
    merged, added = bank_with_regressions(document, turns)
    assert added == 1
    assert merged["questions"][-1] == {
        "id": "hi-r00000000000000a1",
        "text": "दिल्ली से मुंबई कितनी दूर?",
    }
    assert len(bank.parse(json.dumps(merged)).questions) == len(document["questions"]) + 1


def test_the_cli_writes_a_bank_from_the_queue(tmp_path: Path) -> None:
    queue = ReviewQueue(queue_path(tmp_path / "queue", "hi", "s_7f3a9c21"))
    queue.keep(failed("sg_00000000000000a1", "दिल्ली से मुंबई कितनी दूर?"))
    out = tmp_path / "hi.json"
    args = ["bank", "--queue", str(tmp_path / "queue"), "--language", "hi", "--out", str(out)]
    assert main(args) == 0
    written = bank.parse(out.read_text(encoding="utf-8"))
    assert written.questions[-1].id == "hi-r00000000000000a1"


def test_the_cli_writes_golden_candidates_from_the_queue(tmp_path: Path) -> None:
    queue = ReviewQueue(queue_path(tmp_path / "queue", "hi", "s_7f3a9c21"))
    queue.keep(failed("sg_00000000000000a1", "दिल्ली से मुंबई कितनी दूर?"))
    out = tmp_path / "candidates.json"
    args = ["golden", "--queue", str(tmp_path / "queue"), "--language", "hi", "--out", str(out)]
    assert main(args) == 0
    written = json.loads(out.read_text(encoding="utf-8"))
    assert [c["id"] for c in written["manifest"]["clips"]] == ["hi-00000000000000a1"]
