from __future__ import annotations

from typing import Any

from .corpus import LONGEST_S, SELECTION, SHORTEST_S, Row, Sample
from .hub import (
    BACKOFF_S,
    DATASETS,
    DEFAULT_CONFIG,
    MAX_BACKOFF_S,
    RETRIES,
    ROWS,
    Get,
    HubSelection,
    Location,
    audio,
    authorized,
    pin_rows,
    rows_url,
)


def svarah_row(at: Location, row: dict[str, Any]) -> Row:
    accent = f"{row.get('primary_language', '')}/{row.get('native_place_district', '')}"
    return Row(
        at.member,
        float(row["duration"]),
        row["text"],
        accent,
        str(row.get("gender", "")),
    )


def pin_svarah(
    get: Get, source: dict[str, Any], language: str, count: int
) -> tuple[Sample, dict[str, bytes]]:
    rule = SELECTION.format(
        shortest=SHORTEST_S, longest=LONGEST_S, order="row", group="gender", count=count
    ).replace("one clip per speaker", "one clip per first language and district")
    chosen = HubSelection("svarah", DEFAULT_CONFIG, source["languages"][language], svarah_row, rule)
    return pin_rows(get, source, language, count, chosen)


__all__ = [
    "BACKOFF_S",
    "DATASETS",
    "MAX_BACKOFF_S",
    "RETRIES",
    "ROWS",
    "audio",
    "authorized",
    "pin_svarah",
    "rows_url",
    "svarah_row",
]
