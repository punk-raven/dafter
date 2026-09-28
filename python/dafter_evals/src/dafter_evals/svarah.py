from __future__ import annotations

import json
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from typing import Any

from .corpus import LONGEST_S, SELECTION, SHORTEST_S, Clip, Row, Sample, digest, select
from .remote import TIMEOUT_S

ROWS = "https://datasets-server.huggingface.co/rows"
DATASETS = "https://huggingface.co/api/datasets"
PAGE = 100

Get = Callable[[str], bytes]


def authorized(token: str) -> Get:
    def get(url: str) -> bytes:
        request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
        with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:
            data: bytes = response.read()
            return data

    return get


def rows_url(dataset: str, split: str, offset: int, length: int) -> str:
    query = {"dataset": dataset, "config": "default", "split": split}
    query |= {"offset": str(offset), "length": str(length)}
    return f"{ROWS}?{urllib.parse.urlencode(query)}"


def page(get: Get, dataset: str, split: str, offset: int, length: int) -> dict[str, Any]:
    body: dict[str, Any] = json.loads(get(rows_url(dataset, split, offset, length)))
    return body


def member(split: str, index: int) -> str:
    return f"{split}/{index}"


def svarah_row(split: str, index: int, row: dict[str, Any]) -> Row:
    accent = f"{row.get('primary_language', '')}/{row.get('native_place_district', '')}"
    return Row(
        member(split, index),
        float(row["duration"]),
        row["text"],
        accent,
        str(row.get("gender", "")),
    )


def every_row(get: Get, dataset: str, split: str) -> Iterator[Row]:
    offset, total = 0, None
    while total is None or offset < total:
        body = page(get, dataset, split, offset, PAGE)
        total = int(body["num_rows_total"])
        for item in body["rows"]:
            yield svarah_row(split, int(item["row_idx"]), item["row"])
        offset += PAGE


def audio(get: Get, dataset: str, clip_member: str) -> bytes:
    split, index = clip_member.split("/")
    body = page(get, dataset, split, int(index), 1)
    cells = body["rows"][0]["row"]["audio_filepath"]
    return get(cells[0]["src"])


def pin_svarah(
    get: Get, source: dict[str, Any], language: str, count: int
) -> tuple[Sample, dict[str, bytes]]:
    dataset, split = source["dataset"], source["languages"][language]
    revision = json.loads(get(f"{DATASETS}/{dataset}"))["sha"]
    if revision != source["revision"]:
        raise ValueError(f"{dataset} is at {revision}, not the pinned {source['revision']}")
    rows = select(every_row(get, dataset, split), count)
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
    selection = SELECTION.format(
        shortest=SHORTEST_S, longest=LONGEST_S, order="row", group="gender", count=count
    ).replace("one clip per speaker", "one clip per first language and district")
    return Sample("svarah", language, pinned, selection, tuple(clips)), fetched


__all__ = ["audio", "authorized", "every_row", "pin_svarah", "rows_url"]
