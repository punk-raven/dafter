from __future__ import annotations

import io
import shutil
import tarfile
import urllib.request
import wave
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Any

from .corpus import LONGEST_S, SHORTEST_S, Clip, Row, Sample, digest, select
from .remote import TIMEOUT_S, describe

TRANSCRIPTS = ("transcription.txt", "text")
SEGMENTS = "segments"
SPEAKERS = "utt2spk"
LISTS = frozenset({*TRANSCRIPTS, SEGMENTS, SPEAKERS})
WAV = ".wav"
CUT = "#"
HEADER_BYTES = 1 << 16
CHUNK = 1 << 20
ONE_GROUP = "all"
ARCHIVE_PIN = ("archive", "bytes", "etag")
SELECTION = (
    "utterances whose duration is {shortest:g} to {longest:g} s, ordered by audio path; one "
    "clip per speaker (utt2spk, else the segment's recording, else the utterance id up to its "
    "first underscore) until {count} are chosen"
)


@dataclass(frozen=True, slots=True)
class Span:
    recording: str
    start_s: float | None = None
    end_s: float | None = None

    @property
    def member(self) -> str:
        if self.start_s is None or self.end_s is None:
            return self.recording
        return f"{self.recording}{CUT}{self.start_s:.2f}-{self.end_s:.2f}"

    @classmethod
    def parse(cls, member: str) -> Span:
        recording, cut, bounds = member.partition(CUT)
        if not cut:
            return cls(recording)
        start, end = bounds.split("-")
        return cls(recording, float(start), float(end))


class TarArchive:
    def __init__(self, path: Path) -> None:
        self._tar = tarfile.open(path, "r:gz")
        self._members = {m.name: m for m in self._tar.getmembers() if m.isfile()}

    def __enter__(self) -> TarArchive:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self._tar.close()

    @property
    def names(self) -> list[str]:
        return sorted(self._members)

    def read_many(self, names: Iterable[str], limit: int | None = None) -> dict[str, bytes]:
        wanted = sorted(set(names), key=lambda n: self._members[n].offset_data)
        read: dict[str, bytes] = {}
        for name in wanted:
            handle = self._tar.extractfile(self._members[name])
            if handle is None:
                raise ValueError(f"{name} is not a file in the archive")
            read[name] = handle.read(limit) if limit is not None else handle.read()
        return read


@dataclass(frozen=True, slots=True)
class Listing:
    transcripts: dict[str, str]
    segments: dict[str, Span]
    speakers: dict[str, str]
    recordings: dict[str, str]

    def speaker(self, utterance: str) -> str:
        if utterance in self.speakers:
            return self.speakers[utterance]
        if utterance in self.segments:
            return PurePosixPath(self.segments[utterance].recording).stem
        return utterance.split("_", 1)[0]


def text_table(text: str) -> dict[str, str]:
    table: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split(maxsplit=1)
        if parts:
            table[parts[0]] = parts[1].strip() if len(parts) > 1 else ""
    return table


def first_named(tar: TarArchive, basenames: Iterable[str]) -> str:
    for basename in basenames:
        found = [n for n in tar.names if PurePosixPath(n).name == basename]
        if found:
            return tar.read_many(found[:1])[found[0]].decode("utf-8")
    return ""


def listing(tar: TarArchive) -> Listing:
    transcripts = text_table(first_named(tar, TRANSCRIPTS))
    if not transcripts:
        raise ValueError(f"the archive holds none of {', '.join(TRANSCRIPTS)}")
    recordings = {PurePosixPath(n).stem: n for n in tar.names if n.endswith(WAV)}
    segments: dict[str, Span] = {}
    for utterance, rest in text_table(first_named(tar, (SEGMENTS,))).items():
        recording, start, end = rest.split()[:3]
        if recording in recordings:
            cut = Span(recordings[recording], round(float(start), 2), round(float(end), 2))
            segments[utterance] = Span.parse(cut.member)
    speakers = text_table(first_named(tar, (SPEAKERS,)))
    return Listing(transcripts, segments, speakers, recordings)


def wav_seconds(header: bytes, rate_hz: int) -> float:
    with wave.open(io.BytesIO(header)) as w:
        if w.getframerate() != rate_hz:
            raise ValueError(f"audio is {w.getframerate()} Hz, not the pinned {rate_hz} Hz")
        frames: int = w.getnframes()
    return frames / rate_hz


def span_seconds(span: Span) -> float:
    if span.start_s is None or span.end_s is None:
        raise ValueError(f"{span.recording} is a whole recording, not a segment")
    return span.end_s - span.start_s


def utterance_rows(tar: TarArchive, found: Listing, rate_hz: int) -> dict[str, Row]:
    if found.segments:
        return {
            utterance: Row(
                span.member,
                span_seconds(span),
                found.transcripts[utterance],
                found.speaker(utterance),
                ONE_GROUP,
            )
            for utterance, span in found.segments.items()
            if found.transcripts.get(utterance)
        }
    whole = {
        u: found.recordings[u] for u, t in found.transcripts.items() if t and u in found.recordings
    }
    headers = tar.read_many(whole.values(), HEADER_BYTES)
    return {
        utterance: Row(
            member,
            wav_seconds(headers[member], rate_hz),
            found.transcripts[utterance],
            found.speaker(utterance),
            ONE_GROUP,
        )
        for utterance, member in whole.items()
    }


def cut(data: bytes, span: Span, rate_hz: int) -> bytes:
    with wave.open(io.BytesIO(data)) as w:
        if w.getframerate() != rate_hz:
            raise ValueError(f"audio is {w.getframerate()} Hz, not the pinned {rate_hz} Hz")
        if span.start_s is None or span.end_s is None:
            return data
        params = w.getparams()
        w.setpos(round(span.start_s * rate_hz))
        frames = w.readframes(round((span.end_s - span.start_s) * rate_hz))
    out = io.BytesIO()
    with wave.open(out, "wb") as o:
        o.setnchannels(params.nchannels)
        o.setsampwidth(params.sampwidth)
        o.setframerate(rate_hz)
        o.writeframes(frames)
    return out.getvalue()


def extract(tar: TarArchive, members: Iterable[str], rate_hz: int) -> dict[str, bytes]:
    spans = {m: Span.parse(m) for m in members}
    recordings = tar.read_many(s.recording for s in spans.values())
    return {m: cut(recordings[s.recording], s, rate_hz) for m, s in spans.items()}


def pin_mucs(
    archive: Path, source: dict[str, Any], dataset: str, language: str, count: int
) -> tuple[Sample, dict[str, bytes]]:
    rate_hz = int(source["sampleRateHz"])
    with TarArchive(archive) as tar:
        rows = utterance_rows(tar, listing(tar), rate_hz)
        chosen = select(rows.values(), count)
        audio = extract(tar, (r.member for r in chosen), rate_hz)
    ids = {row.member: utterance for utterance, row in rows.items()}
    clips = tuple(
        Clip(
            id=ids[r.member],
            member=r.member,
            speaker=r.speaker,
            duration_s=round(r.duration_s, 4),
            sha256=digest(audio[r.member]),
            reference=r.reference,
        )
        for r in chosen
    )
    archive_pin = source["languages"][language]
    pinned = {
        **{k: source[k] for k in ("sampleRateHz", "license")},
        **{k: archive_pin[k] for k in ARCHIVE_PIN},
    }
    selection = SELECTION.format(shortest=SHORTEST_S, longest=LONGEST_S, count=count)
    sample = Sample(dataset, language, pinned, selection, clips)
    return sample, {c.id: audio[c.member] for c in clips}


def download(entry: Mapping[str, Any], path: Path) -> Path:
    if path.exists() and path.stat().st_size == entry["bytes"]:
        return path
    found = describe(entry["archive"])
    if (found.size, found.etag) != (entry["bytes"], entry["etag"]):
        raise ValueError(f"{entry['archive']} changed since it was pinned: {found}")
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".part")
    with (
        urllib.request.urlopen(entry["archive"], timeout=TIMEOUT_S) as response,
        partial.open("wb") as out,
    ):
        shutil.copyfileobj(response, out, CHUNK)
    if partial.stat().st_size != entry["bytes"]:
        partial.unlink()
        raise ValueError(f"{entry['archive']} arrived truncated")
    partial.replace(path)
    return path


def clip_reader(archive: Callable[[], Path], sample: Sample) -> Callable[[Clip], bytes]:
    rate_hz = int(sample.source["sampleRateHz"])
    extracted: dict[str, bytes] = {}

    def read(clip: Clip) -> bytes:
        if not extracted:
            with TarArchive(archive()) as tar:
                extracted.update(extract(tar, (c.member for c in sample.clips), rate_hz))
        return extracted[clip.member]

    return read


__all__ = [
    "Listing",
    "Span",
    "TarArchive",
    "clip_reader",
    "cut",
    "download",
    "listing",
    "pin_mucs",
    "text_table",
]
