from __future__ import annotations

import json
from pathlib import Path

import pytest
from dafter_core.config import parse
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError
from dafter_core.hashing import canonicalize, hash_document, seal

TESTDATA = Path(__file__).resolve().parents[3] / "testdata"
RFC8785_VECTORS = TESTDATA / "rfc8785"
SHARED_CONFIG = TESTDATA / "config" / "resolved-session-config.json"

CROSS_LANGUAGE_CONFIG_HASH = "e2cbfc6025890c0d0318fc2f734115aa0de90b27465906981f0124be808997f4"


@pytest.mark.parametrize(
    "vector", sorted((RFC8785_VECTORS / "input").glob("*.json")), ids=lambda p: p.name
)
def test_canonicalize_matches_the_reference_vectors(vector: Path) -> None:
    want = (RFC8785_VECTORS / "expected" / vector.name).read_bytes()
    assert canonicalize(vector.read_bytes()) == want


def test_resolved_config_hash_is_pinned_across_both_halves() -> None:
    raw = SHARED_CONFIG.read_bytes()
    assert hash_document(raw) == CROSS_LANGUAGE_CONFIG_HASH
    parse(raw)


def test_the_stamped_hash_is_the_hash_of_the_stamped_document() -> None:
    raw = SHARED_CONFIG.read_bytes()
    document, digest = seal(json.dumps({**json.loads(raw), "configHash": "0" * 64}))
    assert digest == CROSS_LANGUAGE_CONFIG_HASH
    assert hash_document(document) == digest
    assert canonicalize(document) == document


def test_hash_ignores_input_spelling_but_not_content() -> None:
    spaced = '{"b": 1.0,  "a": [2, {"d": "ö", "c": null}]}'
    reordered = '{"a":[2,{"c":null,"d":"ö"}],\n"b":1}'
    changed = '{"a":[2,{"c":null,"d":"ö"}],"b":2}'

    assert hash_document(spaced) == hash_document(reordered)
    assert hash_document(changed) != hash_document(spaced)


def test_a_non_object_document_is_rejected() -> None:
    with pytest.raises(DafterError) as caught:
        hash_document(b"[1, 2]")
    assert caught.value.code is ErrorCode.INVALID_CONFIG
