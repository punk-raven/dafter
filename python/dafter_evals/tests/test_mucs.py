from __future__ import annotations

import io
import tarfile
import wave
from pathlib import Path
from typing import Any

import pytest
from dafter_evals import mucs
from dafter_evals.corpus import Sample, digest, fetch_missing
from dafter_evals.remote import Resource

ARCHIVE_PIN = {"archive": "https://example.invalid/t.tar.gz", "bytes": 1, "etag": '"e"'}
READ_SPEECH = {
    "sampleRateHz": 8000,
    "license": "L",
    "homepage": "h",
    "languages": {"hi": ARCHIVE_PIN},
}
CODESWITCH = {"sampleRateHz": 16000, "license": "CC-BY-SA-4.0", "languages": {"hi": ARCHIVE_PIN}}


def tone(seconds: float, rate: int, level: int = 1) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = int(rate * seconds)
        w.writeframes(b"".join((i % 251 + level).to_bytes(2, "little") for i in range(frames)))
    return out.getvalue()


def tarball(path: Path, files: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def read_speech(path: Path, rate: int = 8000) -> Path:
    utterances = {
        "s01_001": 7.0,
        "s01_002": 8.0,
        "s02_001": 2.0,
        "s02_002": 9.0,
        "s03_001": 10.0,
    }
    files = {
        f"Hindi/test/audio/{u}.wav": tone(s, rate, i) for i, (u, s) in enumerate(utterances.items())
    }
    lines = [f"{u}\tवाक्य {u}" for u in utterances]
    files["Hindi/test/transcription.txt"] = "\n".join(lines).encode()
    return tarball(path, files)


def codeswitch(path: Path) -> Path:
    files = {
        "Hindi-English/test/files/lecture_a.wav": tone(30.0, 16000, 1),
        "Hindi-English/test/files/lecture_b.wav": tone(20.0, 16000, 2),
        "Hindi-English/test/transcripts/text": (
            "a_1 यह lecture है\na_2 next topic\nb_1 हम code लिखते हैं\nb_2 short\n"
        ).encode(),
        "Hindi-English/test/transcripts/segments": (
            b"a_1 lecture_a 1.00 6.50\na_2 lecture_a 7.25 15.00\n"
            b"b_1 lecture_b 2.00 9.75\nb_2 lecture_b 10.00 11.00\n"
        ),
    }
    return tarball(path, files)


def test_a_segment_member_round_trips_with_its_bounds() -> None:
    span = mucs.Span("a/b.wav", 1.0, 6.5)
    assert span.member == "a/b.wav#1.00-6.50"
    assert mucs.Span.parse(span.member) == span
    assert mucs.Span.parse("a/b.wav") == mucs.Span("a/b.wav")


def test_read_speech_is_pinned_one_clip_per_speaker_at_8_khz(tmp_path: Path) -> None:
    archive = read_speech(tmp_path / "hi.tar.gz")
    sample, audio = mucs.pin_mucs(archive, READ_SPEECH, "mucs2021", "hi", 20)
    assert [c.id for c in sample.clips] == ["s01_001", "s02_002", "s03_001"]
    assert [c.speaker for c in sample.clips] == ["s01", "s02", "s03"]
    assert [c.duration_s for c in sample.clips] == [7.0, 9.0, 10.0]
    assert sample.clips[0].reference == "वाक्य s01_001"
    assert sample.source == {"sampleRateHz": 8000, "license": "L", **ARCHIVE_PIN}
    assert all(digest(audio[c.id]) == c.sha256 for c in sample.clips)
    assert Sample.read(sample.dumps()) == sample


def test_audio_at_another_rate_than_the_pinned_one_is_refused(tmp_path: Path) -> None:
    archive = read_speech(tmp_path / "hi.tar.gz", rate=16000)
    with pytest.raises(ValueError, match="not the pinned 8000 Hz"):
        mucs.pin_mucs(archive, READ_SPEECH, "mucs2021", "hi", 20)


def test_code_switched_lectures_are_cut_at_their_segments(tmp_path: Path) -> None:
    archive = codeswitch(tmp_path / "cs.tar.gz")
    sample, audio = mucs.pin_mucs(archive, CODESWITCH, "mucs2021-codeswitch", "hi", 20)
    assert [c.id for c in sample.clips] == ["a_1", "b_1"]
    assert [c.speaker for c in sample.clips] == ["lecture_a", "lecture_b"]
    assert sample.clips[0].member == "Hindi-English/test/files/lecture_a.wav#1.00-6.50"
    assert sample.clips[1].reference == "हम code लिखते हैं"
    with wave.open(io.BytesIO(audio["a_1"])) as w:
        assert (w.getframerate(), w.getnframes()) == (16000, 88000)


def test_fetching_cuts_the_same_bytes_again_from_the_archive(tmp_path: Path) -> None:
    archive = codeswitch(tmp_path / "cs.tar.gz")
    sample, audio = mucs.pin_mucs(archive, CODESWITCH, "mucs2021-codeswitch", "hi", 20)
    opened: list[Path] = []

    def located() -> Path:
        opened.append(archive)
        return archive

    got = fetch_missing(sample, lambda c: False, mucs.clip_reader(located, sample))
    assert {c.id: data for c, data in got} == audio
    assert len(opened) == 1
    assert fetch_missing(sample, lambda c: True, mucs.clip_reader(located, sample)) == []
    assert len(opened) == 1


def test_an_archive_without_a_transcript_is_refused(tmp_path: Path) -> None:
    archive = tarball(tmp_path / "x.tar.gz", {"a/1.wav": tone(5.0, 8000)})
    with pytest.raises(ValueError, match=r"transcription\.txt"):
        mucs.pin_mucs(archive, READ_SPEECH, "mucs2021", "hi", 20)


ENTRY: dict[str, Any] = {
    "archive": "https://example.invalid/Hindi_test.tar.gz",
    "bytes": 4,
    "etag": '"e"',
}


def test_a_downloaded_archive_of_the_pinned_size_is_reused(tmp_path: Path) -> None:
    path = tmp_path / "hi.tar.gz"
    path.write_bytes(b"1234")
    assert mucs.download(ENTRY, path) == path


def test_an_archive_that_changed_upstream_is_never_downloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    changed = Resource(ENTRY["archive"], 4, '"other"', "")
    monkeypatch.setattr(mucs, "describe", lambda url: changed)
    with pytest.raises(ValueError, match="changed since it was pinned"):
        mucs.download(ENTRY, tmp_path / "hi.tar.gz")
    assert not (tmp_path / "hi.tar.gz").exists()
