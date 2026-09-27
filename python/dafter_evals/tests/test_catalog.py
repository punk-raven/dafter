from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from dafter_core.enums import Stage
from dafter_evals.screen import catalog
from dafter_providers import credentials, openai_compat, vendor_for

SOURCES = Path(__file__).resolve().parents[2]
CODE = [
    SOURCES / "dafter_evals" / "src",
    SOURCES / "dafter_providers" / "src" / "dafter_providers" / "openai_compat",
]


def raw() -> dict[str, Any]:
    text = (Path(catalog.__file__).parent / catalog.CATALOG).read_text(encoding="utf-8")
    parsed: dict[str, Any] = json.loads(text)
    return parsed


def test_the_catalog_names_one_candidate_per_stage_4_role() -> None:
    c = catalog.load()
    assert [x.role for x in c.candidates] == [
        "baseline",
        "default",
        "ceiling",
        "latest",
        "open_weights",
        "open_weights",
        "runner_up",
        "free_endpoint",
    ]
    assert c.judge.role == "judge"
    assert str(c.as_of) == "2026-09-27"


def test_every_endpoint_candidate_names_the_endpoint_it_speaks_to() -> None:
    c = catalog.load()
    assert set(c.endpoints) == {"google", "openrouter", "opencode_zen", "openai"}
    served = {x.id: x.endpoint.name for x in c.candidates if x.endpoint is not None}
    assert served["nemotron_lightning_zen"] == "opencode_zen"
    assert served["gemma_4_dense_openrouter"] == "openrouter"
    assert "sarvam_105b" not in served
    listed = {e.name: e.listed_on for e in c.endpoints.values()}
    assert str(listed["opencode_zen"]) == "2026-09-27" and listed["openai"] is None


def test_the_catalog_reads_each_endpoint_binding_from_the_provider() -> None:
    c = catalog.load()
    for endpoint in c.endpoints.values():
        assert endpoint.binding is openai_compat.ENDPOINTS[endpoint.name]
    text = (Path(catalog.__file__).parent / catalog.CATALOG).read_text(encoding="utf-8")
    assert "baseUrl" not in text and "credentialEnv" not in text


def test_every_candidate_builds_through_its_registered_vendor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = catalog.load()
    for candidate in (c.judge, *c.candidates):
        ref = candidate.ref
        assert ref.credential_ref is not None
        monkeypatch.setenv(credentials.env_name(ref.credential_ref), "test-only-not-a-key")
        build = vendor_for(ref, Stage.LLM).llm
        assert build is not None
        assert build(ref).model == ref.model


def test_model_ids_live_in_data_and_never_in_code() -> None:
    c = catalog.load()
    ids = {x.ref.model for x in (c.judge, *c.candidates) if x.ref.model}
    code = "".join(p.read_text(encoding="utf-8") for d in CODE for p in d.rglob("*.py"))
    assert [m for m in ids if m in code] == []


def test_a_claim_no_official_page_states_is_marked_unverified() -> None:
    c = {x.id: x for x in catalog.load().candidates}
    assert not c["gemma_4_moe"].verified
    assert c["openai_mini"].verified
    assert all(u for x in c.values() for u in x.unverified)


def test_price_is_per_token_count_in_the_stated_currency() -> None:
    price = catalog.load().pick(["gemini_flash_lite"])[0].price
    assert price is not None and price.currency == "USD"
    assert price.cost(1_000_000, 1_000_000) == Decimal("2.8")
    assert catalog.load().pick(["gemma_4_moe"])[0].price is None


OPENAI = "secret://evals/openai/api-key"


def options(doc: dict[str, Any]) -> dict[str, Any]:
    found: dict[str, Any] = doc["candidates"][1]["provider"]["options"]
    return found


def broken(change: Any) -> str:
    doc = copy.deepcopy(raw())
    change(doc)
    return json.dumps(doc)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda d: d["candidates"].append(d["candidates"][0]), "repeat"),
        (lambda d: d["candidates"][1]["sources"].__setitem__(0, "http://x"), "https"),
        (lambda d: d["candidates"][1]["provider"].__setitem__("provider", "nobody"), "nobody"),
        (lambda d: d["candidates"][1].__setitem__("freeTier", "yes"), "freeTier"),
        (lambda d: d["candidates"][1].__setitem__("role", "winner"), "role"),
        (lambda d: d["candidates"][1]["price"].__setitem__("currency", "EUR"), "currency"),
        (lambda d: d["candidates"][1]["provider"].pop("model"), "pinned model"),
        (lambda d: d.pop("judge"), "judge"),
        (lambda d: options(d).__setitem__("endpoint", "nowhere"), "nowhere"),
        (lambda d: options(d).pop("endpoint"), "needs a named endpoint"),
        (lambda d: d["endpoints"].__setitem__("groq", d["endpoints"]["openai"]), "binds"),
        (lambda d: d["endpoints"]["google"].__setitem__("baseUrl", "https://x"), "exactly"),
        (lambda d: d["candidates"][1]["provider"].__setitem__("credentialRef", OPENAI), "GEMINI"),
    ],
)
def test_a_malformed_catalog_is_refused(change: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        catalog.parse(broken(change))


def test_picking_an_unknown_candidate_names_it() -> None:
    with pytest.raises(ValueError, match="nobody"):
        catalog.load().pick(["sarvam_105b", "nobody"])
