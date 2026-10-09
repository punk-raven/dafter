from __future__ import annotations

from typing import Any

import pytest
from dafter_core.config import DEFAULT_TURN_DETECTOR, parse
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError

from .test_config import doc


def test_a_turn_that_names_no_detector_runs_the_livekit_one() -> None:
    assert DEFAULT_TURN_DETECTOR == "livekit"
    assert parse(doc()).turn.detector == "livekit"


@pytest.mark.parametrize("detector", ["livekit", "smart_turn"])
def test_a_turn_names_each_detector_the_schema_allows(detector: str) -> None:
    turn = {"strategy": "semantic", "detector": detector}
    assert parse(doc(turn=turn)).turn.detector == detector


@pytest.mark.parametrize("detector", ["multilingual", "", 1])
def test_a_turn_detector_the_schema_does_not_allow_is_refused(detector: Any) -> None:
    with pytest.raises(DafterError) as caught:
        parse(doc(turn={"strategy": "semantic", "detector": detector}))
    assert caught.value.code is ErrorCode.INVALID_CONFIG
    assert any("/turn/detector" in d for d in caught.value.details)
