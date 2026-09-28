from __future__ import annotations

import io
import json
import wave
import zipfile
from typing import Any

import pytest
from dafter_evals import svarah
from dafter_evals.corpus import Archive, Row, Sample, digest, fetch_missing, pin_kathbath, select

SOURCE = {
    "archive": "https://example.invalid/kathbath.zip",
    "bytes": 0,
    "etag": '"e"',
    "license": "CC-BY-4.0",
    "languages": {"kn-IN": "kannada"},
}


def wav(seconds: float, rate: int = 16000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(rate * seconds))
    return out.getvalue()


def row(member: str, seconds: float, speaker: str, group: str) -> Row:
    return Row(member, seconds, "ಪಠ್ಯ", speaker, group)


def test_the_sample_spreads_over_speakers_and_groups_within_the_duration_bounds() -> None:
    rows = [
        row("a/1", 5, "s1", "f"),
        row("a/2", 5, "s1", "f"),
        row("a/3", 2, "s2", "f"),
        row("a/4", 13, "s3", "m"),
        row("a/5", 6, "s4", "m"),
        row("a/6", 7, "s5", "f"),
        row("a/7", 8, "s6", "m"),
        row("a/8", 9, "s7", "m"),
    ]
    chosen = [r.member for r in select(reversed(rows), 4)]
    assert chosen == ["a/1", "a/5", "a/6", "a/7"]
    assert [r.member for r in select(rows, 10)] == ["a/1", "a/5", "a/6", "a/7", "a/8"]


def kathbath_zip() -> bytes:
    paths = [f"kathbath/kannada/wavs/{i}-{i % 3}-{'f' if i % 2 else 'm'}.wav" for i in range(6)]
    lines = [
        json.dumps({"audio_filepath": p, "duration": 4.0 + i, "text": f"ವಾಕ್ಯ {i}"})
        for i, p in enumerate(paths)
    ]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("kathbath/kannada/manifest.json", "\n".join(lines))
        for i, p in enumerate(paths):
            z.writestr(p, wav(0.1 * (1 + i)))
    return out.getvalue()


def archive(data: bytes) -> Archive:
    return Archive(len(data), lambda start, end: data[start:end])


def test_a_kathbath_sample_is_read_from_the_archive_by_range_and_pinned_by_digest() -> None:
    data = kathbath_zip()
    opened = archive(data)
    sample, audio = pin_kathbath(opened, SOURCE, "kn-IN", 2)
    assert opened.fetched < len(data) * 2
    assert [c.id for c in sample.clips] == ["1-1-f", "0-0-m"]
    assert all(digest(audio[c.id]) == c.sha256 for c in sample.clips)
    assert sample.clips[0].reference == "ವಾಕ್ಯ 1"
    assert sample.source == {k: SOURCE[k] for k in ("archive", "bytes", "etag", "license")}
    again = Sample.read(sample.dumps())
    assert again == sample
    assert again.seconds == pytest.approx(9.0)


def test_a_fetched_clip_that_changed_is_refused() -> None:
    data = kathbath_zip()
    sample, _ = pin_kathbath(archive(data), SOURCE, "kn-IN", 2)
    got = fetch_missing(sample, lambda c: c.id == "0-0-m", lambda c: archive(data).read(c.member))
    assert [c.id for c, _ in got] == ["1-1-f"]
    with pytest.raises(ValueError, match="sha256"):
        fetch_missing(sample, lambda c: False, lambda c: b"changed")


class Hub:
    def __init__(self, revision: str) -> None:
        self.revision = revision
        self.rows = [
            {
                "duration": 4.0 + i,
                "text": f"sentence {i}",
                "gender": "Female" if i % 2 else "Male",
                "primary_language": ["Kannada", "Tamil", "Hindi"][i % 3],
                "native_place_district": "D",
                "audio_filepath": [
                    {"src": f"https://hub.invalid/audio/{i}.wav", "type": "audio/wav"}
                ],
            }
            for i in range(5)
        ]

    def get(self, url: str) -> bytes:
        if url.startswith(svarah.DATASETS):
            return json.dumps({"sha": self.revision}).encode()
        if url.startswith(svarah.ROWS):
            q = dict(p.split("=") for p in url.split("?")[1].split("&"))
            offset, length = int(q["offset"]), int(q["length"])
            items = [
                {"row_idx": i, "row": self.rows[i]}
                for i in range(offset, min(offset + length, len(self.rows)))
            ]
            body: dict[str, Any] = {"rows": items, "num_rows_total": len(self.rows)}
            return json.dumps(body).encode()
        return wav(0.05 * (1 + int(url.rsplit("/", 1)[1].split(".")[0])))


SVARAH = {
    "dataset": "ai4bharat/Svarah",
    "revision": "r1",
    "license": "CC-BY-4.0",
    "languages": {"en-IN": "test"},
}


def test_a_svarah_sample_spreads_over_first_languages_at_the_pinned_revision() -> None:
    hub = Hub("r1")
    sample, audio = svarah.pin_svarah(hub.get, SVARAH, "en-IN", 3)
    assert [c.member for c in sample.clips] == ["test/1", "test/0", "test/2"]
    assert [c.speaker for c in sample.clips] == ["Tamil/D", "Kannada/D", "Hindi/D"]
    assert all(digest(audio[c.id]) == c.sha256 for c in sample.clips)
    assert svarah.audio(hub.get, "ai4bharat/Svarah", "test/2") == audio["test-2"]
    assert "first language" in sample.selection
    with pytest.raises(ValueError, match="pinned"):
        svarah.pin_svarah(Hub("r2").get, SVARAH, "en-IN", 3)
