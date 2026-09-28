from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from .remote import Fetch, RangeFile

SHORTEST_S = 3.0
LONGEST_S = 12.0
BUFFER = 1 << 16


@dataclass(frozen=True, slots=True)
class Row:
    member: str
    duration_s: float
    reference: str
    speaker: str
    group: str


@dataclass(frozen=True, slots=True)
class Clip:
    id: str
    member: str
    speaker: str
    duration_s: float
    sha256: str
    reference: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "member": self.member,
            "speaker": self.speaker,
            "durationS": self.duration_s,
            "sha256": self.sha256,
            "reference": self.reference,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Clip:
        return cls(
            id=d["id"],
            member=d["member"],
            speaker=d["speaker"],
            duration_s=float(d["durationS"]),
            sha256=d["sha256"],
            reference=d["reference"],
        )


@dataclass(frozen=True, slots=True)
class Sample:
    dataset: str
    language: str
    source: dict[str, Any]
    selection: str
    clips: tuple[Clip, ...]

    @property
    def seconds(self) -> float:
        return sum(c.duration_s for c in self.clips)

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "language": self.language,
            "source": self.source,
            "selection": self.selection,
            "clips": [c.to_dict() for c in self.clips],
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Sample:
        return cls(
            dataset=d["dataset"],
            language=d["language"],
            source=d["source"],
            selection=d["selection"],
            clips=tuple(Clip.from_dict(c) for c in d["clips"]),
        )

    @classmethod
    def read(cls, text: str) -> Sample:
        return cls.from_dict(json.loads(text))

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"


SELECTION = (
    "rows whose duration is {shortest:g} to {longest:g} s, ordered by {order}; one clip per "
    "speaker, taken in turn from each {group} group, until {count} are chosen"
)


def select(rows: Iterable[Row], count: int) -> list[Row]:
    eligible = sorted(
        (r for r in rows if SHORTEST_S <= r.duration_s <= LONGEST_S), key=lambda r: r.member
    )
    groups = [[r for r in eligible if r.group == g] for g in sorted({r.group for r in eligible})]
    chosen: list[Row] = []
    speakers: set[str] = set()
    while len(chosen) < count and any(groups):
        for group in groups:
            while group and group[0].speaker in speakers:
                group.pop(0)
            if group and len(chosen) < count:
                row = group.pop(0)
                speakers.add(row.speaker)
                chosen.append(row)
    return chosen


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def kathbath_row(line: str) -> Row:
    d = json.loads(line)
    member = d["audio_filepath"]
    _, speaker, gender = PurePosixPath(member).stem.rsplit("-", 2)
    return Row(member, float(d["duration"]), d["text"], speaker, gender)


class Archive:
    def __init__(self, size: int, fetch: Fetch) -> None:
        self._file = RangeFile(size, fetch)
        self._zip = zipfile.ZipFile(io.BufferedReader(self._file, buffer_size=BUFFER))

    @property
    def fetched(self) -> int:
        return self._file.fetched

    def read(self, member: str) -> bytes:
        return self._zip.read(member)


def pin_kathbath(
    archive: Archive, source: dict[str, Any], language: str, count: int
) -> tuple[Sample, dict[str, bytes]]:
    folder = source["languages"][language]
    manifest = archive.read(f"kathbath/{folder}/manifest.json").decode("utf-8")
    rows = select((kathbath_row(line) for line in manifest.splitlines() if line.strip()), count)
    audio = {PurePosixPath(r.member).stem: archive.read(r.member) for r in rows}
    clips = tuple(
        Clip(
            id=PurePosixPath(r.member).stem,
            member=r.member,
            speaker=r.speaker,
            duration_s=round(r.duration_s, 4),
            sha256=digest(audio[PurePosixPath(r.member).stem]),
            reference=r.reference,
        )
        for r in rows
    )
    pinned = {k: source[k] for k in ("archive", "bytes", "etag", "license")}
    selection = SELECTION.format(
        shortest=SHORTEST_S, longest=LONGEST_S, order="audio path", group="gender", count=count
    )
    return Sample("kathbath", language, pinned, selection, clips), audio


def verified(clip: Clip, data: bytes) -> bytes:
    if digest(data) != clip.sha256:
        raise ValueError(f"{clip.id} does not match its pinned sha256; the source has changed")
    return data


Reader = Callable[[Clip], bytes]


def fetch_missing(
    sample: Sample, have: Callable[[Clip], bool], read: Reader
) -> Sequence[tuple[Clip, bytes]]:
    return [(c, verified(c, read(c))) for c in sample.clips if not have(c)]


__all__ = [
    "Archive",
    "Clip",
    "Row",
    "Sample",
    "digest",
    "fetch_missing",
    "kathbath_row",
    "pin_kathbath",
    "select",
    "verified",
]
