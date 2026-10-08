from __future__ import annotations

import json
from typing import Any

import pytest
from dafter_core.config import parse
from dafter_core.enums import AddressingMode, ErrorCode
from dafter_core.errors import DafterError

from .test_config import MINIMAL


def with_addressing(block: Any, name: str | None = "Nivya") -> str:
    d = json.loads(json.dumps(MINIMAL))
    if name is not None:
        d["agent"]["name"] = name
    d["agent"]["addressing"] = block
    return json.dumps(d)


def rejected(block: Any, name: str | None = "Nivya") -> DafterError:
    with pytest.raises(DafterError) as caught:
        parse(with_addressing(block, name))
    return caught.value


def test_a_document_without_addressing_answers_always() -> None:
    addressing = parse(json.dumps(MINIMAL)).agent.addressing
    assert addressing.mode is AddressingMode.ALWAYS
    assert not addressing.waits_to_be_called


@pytest.mark.parametrize("mode", list(AddressingMode))
def test_every_addressing_mode_parses_with_a_name(mode: AddressingMode) -> None:
    agent = parse(
        with_addressing(
            {
                "mode": str(mode),
                "aliases": ["निव्या"],
                "nearMisses": ["Navya"],
                "followUpWindowMs": 15000,
            }
        )
    ).agent
    a = agent.addressing
    assert (a.mode, agent.name, a.aliases, a.near_misses, a.follow_up_window_ms) == (
        mode,
        "Nivya",
        ("निव्या",),
        ("Navya",),
        15000,
    )


def test_an_agent_can_stay_awake_until_told_to_sleep() -> None:
    awake = parse(with_addressing({"mode": "transcript", "staysAwake": True})).agent.addressing
    assert awake.stays_awake
    assert not parse(with_addressing({"mode": "transcript"})).agent.addressing.stays_awake
    assert rejected({"mode": "transcript", "staysAwake": "yes"}).code is ErrorCode.INVALID_CONFIG


def test_always_needs_no_name() -> None:
    assert parse(with_addressing({"mode": "always"}, name=None)).agent.name is None


@pytest.mark.parametrize("mode", ["transcript", "on_device"])
def test_a_mode_that_waits_to_be_called_needs_a_name(mode: str) -> None:
    err = rejected({"mode": mode}, name=None)
    assert err.code is ErrorCode.INVALID_CONFIG
    assert any("/agent/name" in d for d in err.details), err.details


@pytest.mark.parametrize("miss", ["Nivya", "निव्या"])
def test_a_near_miss_cannot_be_the_name_or_an_alias(miss: str) -> None:
    err = rejected({"mode": "transcript", "aliases": ["निव्या"], "nearMisses": ["Navya", miss]})
    assert err.code is ErrorCode.INVALID_CONFIG
    assert any("/agent/addressing/nearMisses" in d for d in err.details), err.details


@pytest.mark.parametrize(
    "block",
    [
        {"aliases": ["निव्या"]},
        {"mode": "wake_word"},
        {"mode": "transcript", "name": "Nivya"},
        {"mode": "transcript", "followUpWindowMs": 999},
        {"mode": "transcript", "followUpWindowMs": 120001},
        {"mode": "transcript", "aliases": ["निव्या", "निव्या"]},
        {"mode": "transcript", "wakeWord": "Nivya"},
        {"mode": "transcript", "aliases": [7]},
    ],
    ids=[
        "no mode",
        "unknown mode",
        "a name of its own",
        "window too short",
        "window too long",
        "duplicate alias",
        "unknown field",
        "alias not a string",
    ],
)
def test_addressing_outside_its_bounds_is_rejected(block: dict[str, Any]) -> None:
    assert rejected(block).code is ErrorCode.INVALID_CONFIG
