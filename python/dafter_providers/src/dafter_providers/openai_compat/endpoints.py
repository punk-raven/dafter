from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from types import MappingProxyType

from .. import credentials

TABLE = "endpoints.json"
FIELDS = frozenset({"baseUrl", "credentialEnv"})
VENDOR_NAME = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


@dataclass(frozen=True, slots=True)
class Endpoint:
    name: str
    base_url: str
    credential_env: str


def _endpoint(name: str, raw: object) -> Endpoint:
    if not VENDOR_NAME.match(name):
        raise ValueError(f"endpoint {name}: a provider name, lowercase letters, digits and _")
    if not isinstance(raw, dict) or set(raw) != FIELDS:
        raise ValueError(f"endpoint {name}: exactly {', '.join(sorted(FIELDS))}")
    endpoint = Endpoint(name=name, base_url=raw["baseUrl"], credential_env=raw["credentialEnv"])
    if not isinstance(endpoint.base_url, str) or not endpoint.base_url.startswith("https://"):
        raise ValueError(f"endpoint {name}: baseUrl is an https URL")
    if endpoint.credential_env not in credentials.PROVIDER_CREDENTIALS:
        raise ValueError(f"endpoint {name}: {endpoint.credential_env} is not a provider credential")
    return endpoint


def parse(text: str) -> Mapping[str, Endpoint]:
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("the endpoint table maps names to endpoints")
    table = {name: _endpoint(name, entry) for name, entry in raw.items()}
    keys = [e.credential_env for e in table.values()]
    shared = sorted({k for k in keys if keys.count(k) > 1})
    if shared:
        raise ValueError(f"{', '.join(shared)} is bound to more than one endpoint")
    return MappingProxyType(table)


def load() -> Mapping[str, Endpoint]:
    return parse(resources.files(__package__).joinpath(TABLE).read_text(encoding="utf-8"))


ENDPOINTS = load()
