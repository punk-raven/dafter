from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from dafter_core import schemas
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError
from dafter_core.validation import validate_document

VECTORS = Path(__file__).resolve().parents[3] / "testdata" / "agents" / "definitions.json"
FILE: dict[str, Any] = json.loads(VECTORS.read_text())
POINTER = re.compile(r"^at '([^']*)'")


def validate(document: Any) -> Any:
    raw = json.dumps(document).encode()
    return validate_document(schemas.AGENT_DEFINITION, raw, ErrorCode.INVALID_CONFIG)


@pytest.mark.parametrize("case", FILE["cases"], ids=[c["name"] for c in FILE["cases"]])
def test_agent_definitions_match_the_shared_vectors(case: dict[str, Any]) -> None:
    rejected = case["rejected"]
    if rejected is None:
        assert validate(case["document"]) == case["document"]
        return
    with pytest.raises(DafterError) as caught:
        validate(case["document"])
    assert caught.value.code is ErrorCode(rejected["code"])
    pointers = sorted({m.group(1) for d in caught.value.details if (m := POINTER.match(d))})
    assert pointers == rejected["pointers"]


def test_an_agent_definition_states_nothing_but_its_own_fields() -> None:
    with pytest.raises(DafterError) as caught:
        validate({"name": "Asha", "voice": "priya"})
    assert caught.value.code is ErrorCode.INVALID_CONFIG
    assert any("voice" in d for d in caught.value.details), caught.value.details
