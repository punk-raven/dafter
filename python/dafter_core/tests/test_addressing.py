from __future__ import annotations

import json
from typing import Any

import pytest
from dafter_core.config import parse
from dafter_core.enums import AddressingMode, ErrorCode
from dafter_core.errors import DafterError

from .test_config import MINIMAL


def with_addressing(block: Any) -> str:
    d = json.loads(json.dumps(MINIMAL))
    d["agent"]["addressing"] = block
    return json.dumps(d)


def rejected(block: Any) -> DafterError:
    with pytest.raises(DafterError) as caught:
        parse(with_addressing(block))
    return caught.value


def test_a_document_without_addressing_answers_always() -> None:
    addressing = parse(json.dumps(MINIMAL)).agent.addressing
    assert addressing.mode is AddressingMode.ALWAYS
    assert not addressing.waits_to_be_called


@pytest.mark.parametrize("mode", list(AddressingMode))
def test_every_addressing_mode_parses_with_a_name(mode: AddressingMode) -> None:
    a = parse(
        with_addressing(
            {
                "mode": str(mode),
                "name": "Nivya",
                "aliases": ["निव्या"],
                "nearMisses": ["Navya"],
                "followUpWindowMs": 15000,
            }
        )
    ).agent.addressing
    assert (a.mode, a.name, a.aliases, a.near_misses, a.follow_up_window_ms) == (
        mode,
        "Nivya",
        ("निव्या",),
        ("Navya",),
        15000,
    )


def test_always_needs_no_name() -> None:
    assert parse(with_addressing({"mode": "always"})).agent.addressing.name == ""


@pytest.mark.parametrize("mode", ["transcript", "on_device"])
def test_a_mode_that_waits_to_be_called_needs_a_name(mode: str) -> None:
    err = rejected({"mode": mode})
    assert err.code is ErrorCode.INVALID_CONFIG
    assert any("/agent/addressing/name" in d for d in err.details), err.details


@pytest.mark.parametrize("miss", ["Nivya", "निव्या"])
def test_a_near_miss_cannot_be_the_name_or_an_alias(miss: str) -> None:
    err = rejected(
        {"mode": "transcript", "name": "Nivya", "aliases": ["निव्या"], "nearMisses": ["Navya", miss]}
    )
    assert err.code is ErrorCode.INVALID_CONFIG
    assert any("/agent/addressing/nearMisses" in d for d in err.details), err.details


@pytest.mark.parametrize(
    "block",
    [
        {"name": "Nivya"},
        {"mode": "wake_word", "name": "Nivya"},
        {"mode": "transcript", "name": ""},
        {"mode": "transcript", "name": "Nivya", "followUpWindowMs": 999},
        {"mode": "transcript", "name": "Nivya", "followUpWindowMs": 120001},
        {"mode": "transcript", "name": "Nivya", "aliases": ["निव्या", "निव्या"]},
        {"mode": "transcript", "name": "Nivya", "wakeWord": "Nivya"},
        {"mode": "transcript", "name": "Nivya", "aliases": [7]},
    ],
    ids=[
        "no mode",
        "unknown mode",
        "empty name",
        "window too short",
        "window too long",
        "duplicate alias",
        "unknown field",
        "alias not a string",
    ],
)
def test_addressing_outside_its_bounds_is_rejected(block: dict[str, Any]) -> None:
    assert rejected(block).code is ErrorCode.INVALID_CONFIG
