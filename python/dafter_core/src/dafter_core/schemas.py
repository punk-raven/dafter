from __future__ import annotations

import json
from importlib import resources
from typing import Any

_ROOT = "dafter_core._schemas"

RESOLVED_SESSION_CONFIG = "config/v1/resolved-session-config.schema.json"
EVENT_ENVELOPE = "events/v1/envelope.schema.json"
ERROR = "errors/v1/error.schema.json"
IDS = "common/v1/ids.schema.json"


def load(relative: str) -> dict[str, Any]:
    package, _, name = relative.rpartition("/")
    anchor = f"{_ROOT}.{package.replace('/', '.')}" if package else _ROOT
    text = resources.files(anchor).joinpath(name).read_text(encoding="utf-8")
    doc: dict[str, Any] = json.loads(text)
    return doc


def enum_at(relative: str, *keys: str) -> set[str]:
    node: Any = load(relative)
    for key in keys:
        node = node[key]
    return set(node)
