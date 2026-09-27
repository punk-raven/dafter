from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from importlib import resources
from typing import Any

from dafter_core.config import ProviderRef
from dafter_providers import VENDORS, credentials

PACKAGE = "dafter_evals.screen"
CATALOG = "candidates.json"
ROLES = frozenset(
    {
        "baseline",
        "default",
        "ceiling",
        "latest",
        "open_weights",
        "runner_up",
        "free_endpoint",
        "judge",
    }
)
CURRENCIES = frozenset({"INR", "USD"})


@dataclass(frozen=True, slots=True)
class Price:
    currency: str
    input: Decimal
    output: Decimal
    per: Decimal
    source: str

    def cost(self, input_tokens: int, output_tokens: int) -> Decimal:
        spent = self.input * input_tokens + self.output * output_tokens
        return spent / self.per


@dataclass(frozen=True, slots=True)
class Endpoint:
    name: str
    base_url: str
    credential_env: str
    model_list: str | None
    listed_on: date | None
    docs: str


@dataclass(frozen=True, slots=True)
class Candidate:
    id: str
    role: str
    family: str
    endpoint: Endpoint | None
    ref: ProviderRef
    price: Price | None
    free_tier: bool | None
    sources: tuple[str, ...]
    unverified: tuple[str, ...]
    notes: str

    @property
    def verified(self) -> bool:
        return not self.unverified


@dataclass(frozen=True, slots=True)
class Catalog:
    as_of: date
    endpoints: dict[str, Endpoint]
    judge: Candidate
    candidates: tuple[Candidate, ...]

    def pick(self, ids: list[str] | None) -> tuple[Candidate, ...]:
        if not ids:
            return self.candidates
        known = {c.id: c for c in self.candidates}
        missing = [i for i in ids if i not in known]
        if missing:
            raise ValueError(f"not in the catalog: {', '.join(missing)}")
        return tuple(known[i] for i in ids)


def _https(where: str, url: object) -> str:
    if not isinstance(url, str) or not url.startswith("https://"):
        raise ValueError(f"{where}: every source is an https URL")
    return url


def _price(where: str, raw: object) -> Price | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or raw.get("currency") not in CURRENCIES:
        raise ValueError(f"{where}: price needs a currency, one of {', '.join(sorted(CURRENCIES))}")
    try:
        price = Price(
            currency=raw["currency"],
            input=Decimal(str(raw["input"])),
            output=Decimal(str(raw["output"])),
            per=Decimal(str(raw["per"])),
            source=_https(where, raw["source"]),
        )
    except KeyError as exc:
        raise ValueError(f"{where}: price lacks {exc}") from exc
    if price.input < 0 or price.output < 0 or price.per <= 0:
        raise ValueError(f"{where}: prices are >= 0 per a positive token count")
    return price


def _endpoint(name: str, raw: dict[str, Any]) -> Endpoint:
    where = f"endpoint {name}"
    listed = raw["listedOn"]
    model_list = raw["modelList"]
    return Endpoint(
        name=name,
        base_url=_https(where, raw["baseUrl"]),
        credential_env=raw["credentialEnv"],
        model_list=None if model_list is None else _https(where, model_list),
        listed_on=None if listed is None else date.fromisoformat(listed),
        docs=_https(where, raw["docs"]),
    )


def _served_by(
    where: str, ref: ProviderRef, name: object, endpoints: dict[str, Endpoint]
) -> Endpoint | None:
    if name is None:
        if "baseUrl" in ref.options:
            raise ValueError(f"{where}: an endpoint URL needs a named endpoint")
        return None
    endpoint = endpoints.get(str(name))
    if endpoint is None:
        raise ValueError(f"{where}: names endpoint {name}, which the catalog does not list")
    if ref.options.get("baseUrl") != endpoint.base_url:
        raise ValueError(f"{where}: baseUrl is not endpoint {name}'s")
    if credentials.env_name(ref.credential_ref or "") != endpoint.credential_env:
        raise ValueError(f"{where}: credential does not read {endpoint.credential_env}")
    return endpoint


def _candidate(raw: dict[str, Any], endpoints: dict[str, Endpoint]) -> Candidate:
    where = f"candidate {raw.get('id', '?')}"
    ref = ProviderRef.from_dict(raw["provider"])
    vendor = VENDORS.get(ref.provider)
    if vendor is None or vendor.llm is None:
        raise ValueError(f"{where}: {ref.provider} is no registered llm provider")
    if not ref.model or not ref.credential_ref:
        raise ValueError(f"{where}: needs a pinned model and a credential reference")
    if raw["role"] not in ROLES:
        raise ValueError(f"{where}: role is one of {', '.join(sorted(ROLES))}")
    free_tier = raw["freeTier"]
    if free_tier is not None and not isinstance(free_tier, bool):
        raise ValueError(f"{where}: freeTier is true, false or null when no source states it")
    sources = tuple(_https(where, s) for s in raw["sources"])
    if not sources:
        raise ValueError(f"{where}: at least one source")
    return Candidate(
        id=raw["id"],
        role=raw["role"],
        family=raw["family"],
        endpoint=_served_by(where, ref, raw.get("endpoint"), endpoints),
        ref=ref,
        price=_price(where, raw["price"]),
        free_tier=free_tier,
        sources=sources,
        unverified=tuple(str(u) for u in raw["unverified"]),
        notes=raw["notes"],
    )


def parse(text: str) -> Catalog:
    raw = json.loads(text)
    try:
        endpoints = {n: _endpoint(n, e) for n, e in raw["endpoints"].items()}
        judge = _candidate(raw["judge"], endpoints)
        candidates = tuple(_candidate(c, endpoints) for c in raw["candidates"])
        as_of = date.fromisoformat(raw["asOf"])
    except KeyError as exc:
        raise ValueError(f"the catalog lacks {exc}") from exc
    ids = [c.id for c in (judge, *candidates)]
    if len(set(ids)) != len(ids):
        raise ValueError("candidate ids repeat")
    return Catalog(as_of=as_of, endpoints=endpoints, judge=judge, candidates=candidates)


def load(text: str | None = None) -> Catalog:
    if text is None:
        text = resources.files(PACKAGE).joinpath(CATALOG).read_text(encoding="utf-8")
    return parse(text)
