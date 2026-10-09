from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

from .corpus import Clip, Row, Sample, digest, select
from .remote import TIMEOUT_S

ROWS = "https://datasets-server.huggingface.co/rows"
DATASETS = "https://huggingface.co/api/datasets"
DEFAULT_CONFIG = "default"
AUDIO_COLUMN = "audio_filepath"
PAGE = 100
RETRIES = 8
RETRYABLE = frozenset({429, 500, 502, 503, 504})
BACKOFF_S = 15
MAX_BACKOFF_S = 240

Get = Callable[[str], bytes]


@dataclass(frozen=True, slots=True)
class Location:
    config: str
    split: str
    index: int

    @property
    def member(self) -> str:
        if self.config == DEFAULT_CONFIG:
            return f"{self.split}/{self.index}"
        return f"{self.config}/{self.split}/{self.index}"

    @classmethod
    def parse(cls, member: str) -> Location:
        parts = member.split("/")
        if len(parts) == 2:
            return cls(DEFAULT_CONFIG, parts[0], int(parts[1]))
        config, split, index = parts
        return cls(config, split, int(index))


RowParser = Callable[[Location, dict[str, Any]], Row]


def authorized(token: str, sleep: Callable[[float], None] = time.sleep) -> Get:
    def once(url: str) -> bytes:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            data: bytes = response.read()
            return data

    def get(url: str) -> bytes:
        for attempt in range(RETRIES):
            try:
                return once(url)
            except urllib.error.HTTPError as error:
                if error.code not in RETRYABLE:
                    raise
                wait = min(BACKOFF_S * 2**attempt, MAX_BACKOFF_S)
                print(f"HTTP {error.code}, retrying in {wait}s", file=sys.stderr)
                sleep(wait)
        return once(url)

    return get


def rows_url(
    dataset: str, split: str, offset: int, length: int, config: str = DEFAULT_CONFIG
) -> str:
    query = {"dataset": dataset, "config": config, "split": split}
    query |= {"offset": str(offset), "length": str(length)}
    return f"{ROWS}?{urllib.parse.urlencode(query)}"


def page(
    get: Get, dataset: str, split: str, offset: int, length: int, config: str = DEFAULT_CONFIG
) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(get(rows_url(dataset, split, offset, length, config)))
    return body


def every_row(
    get: Get, dataset: str, config: str, split: str, parse_row: RowParser
) -> Iterator[Row]:
    offset, total = 0, None
    while total is None or offset < total:
        body = page(get, dataset, split, offset, PAGE, config)
        total = int(body["num_rows_total"])
        for item in body["rows"]:
            row = parse_row(Location(config, split, int(item["row_idx"])), item["row"])
            if row.reference.strip():
                yield row
        offset += PAGE


def audio(get: Get, dataset: str, clip_member: str) -> bytes:
    at = Location.parse(clip_member)
    body = page(get, dataset, at.split, at.index, 1, at.config)
    cells = body["rows"][0]["row"][AUDIO_COLUMN]
    return get(cells[0]["src"])


def check_revision(get: Get, source: dict[str, Any]) -> None:
    dataset = source["dataset"]
    revision = json.loads(get(f"{DATASETS}/{dataset}"))["sha"]
    if revision != source["revision"]:
        raise ValueError(f"{dataset} is at {revision}, not the pinned {source['revision']}")


@dataclass(frozen=True, slots=True)
class HubSelection:
    name: str
    config: str
    split: str
    parse_row: RowParser
    rule: str


def pin_rows(
    get: Get, source: dict[str, Any], language: str, count: int, chosen: HubSelection
) -> tuple[Sample, dict[str, bytes]]:
    check_revision(get, source)
    dataset = source["dataset"]
    rows = select(every_row(get, dataset, chosen.config, chosen.split, chosen.parse_row), count)
    clips: list[Clip] = []
    fetched: dict[str, bytes] = {}
    for row in rows:
        data = audio(get, dataset, row.member)
        clip_id = row.member.replace("/", "-")
        fetched[clip_id] = data
        clips.append(
            Clip(
                clip_id,
                row.member,
                row.speaker,
                round(row.duration_s, 4),
                digest(data),
                row.reference,
            )
        )
    pinned = {k: source[k] for k in ("dataset", "revision", "license")}
    return Sample(chosen.name, language, pinned, chosen.rule, tuple(clips)), fetched


__all__ = [
    "BACKOFF_S",
    "DATASETS",
    "DEFAULT_CONFIG",
    "MAX_BACKOFF_S",
    "RETRIES",
    "ROWS",
    "Get",
    "HubSelection",
    "Location",
    "audio",
    "authorized",
    "every_row",
    "pin_rows",
    "rows_url",
]
