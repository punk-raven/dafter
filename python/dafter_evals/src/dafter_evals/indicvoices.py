from __future__ import annotations

from typing import Any

from .corpus import LONGEST_S, SELECTION, SHORTEST_S, Row, Sample
from .hub import DEFAULT_CONFIG, Get, HubSelection, Location, pin_rows

INDICVOICES = "indicvoices"
LAHAJA = "lahaja"


def indicvoices_row(at: Location, row: dict[str, Any]) -> Row:
    return Row(
        at.member,
        float(row["duration"]),
        row["text"],
        str(row["speaker_id"]),
        str(row.get("gender", "")),
    )


def lahaja_row(at: Location, row: dict[str, Any]) -> Row:
    return Row(
        at.member,
        float(row["duration"]),
        row["text"],
        str(row["sp_id"]),
        str(row.get("gender", "")),
    )


def rule(count: int) -> str:
    return SELECTION.format(
        shortest=SHORTEST_S, longest=LONGEST_S, order="row", group="gender", count=count
    )


def pin_indicvoices(
    get: Get, source: dict[str, Any], language: str, count: int
) -> tuple[Sample, dict[str, bytes]]:
    config = source["languages"][language]
    chosen = HubSelection(INDICVOICES, config, source["split"], indicvoices_row, rule(count))
    return pin_rows(get, source, language, count, chosen)


def pin_lahaja(
    get: Get, source: dict[str, Any], language: str, count: int
) -> tuple[Sample, dict[str, bytes]]:
    split = source["languages"][language]
    chosen = HubSelection(LAHAJA, DEFAULT_CONFIG, split, lahaja_row, rule(count))
    return pin_rows(get, source, language, count, chosen)


__all__ = [
    "INDICVOICES",
    "LAHAJA",
    "indicvoices_row",
    "lahaja_row",
    "pin_indicvoices",
    "pin_lahaja",
]
