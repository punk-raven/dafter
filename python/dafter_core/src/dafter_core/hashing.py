from __future__ import annotations

import hashlib
import json
from typing import Any

import rfc8785

from .enums import ErrorCode
from .errors import DafterError


def canonicalize(raw: bytes | str) -> bytes:
    """RFC 8785.

    "Canonical JSON" is not one thing: Go and Python order keys, escape unicode
    and render numbers differently, so an unnamed scheme yields two hashes for
    one document and proves nothing at the moment somebody audits a specific
    call.
    """
    try:
        return rfc8785.dumps(json.loads(raw))
    except (ValueError, rfc8785.CanonicalizationError) as exc:
        raise DafterError(ErrorCode.INVALID_CONFIG, f"canonicalize document: {exc}") from exc


CONFIG_HASH = "configHash"


def hash_document(raw: bytes | str, field: str = CONFIG_HASH) -> str:
    """Hex SHA-256 over the canonicalization of raw with its own hash field removed.

    A stored document carries the hash of itself, so a reader can recompute it.
    """
    doc = _object(raw)
    doc.pop(field, None)
    return hashlib.sha256(rfc8785.dumps(doc)).hexdigest()


def seal(raw: bytes | str, field: str = CONFIG_HASH) -> tuple[bytes, str]:
    """Stamp the document with its own hash and return the canonical form."""
    digest = hash_document(raw, field)
    doc = _object(raw)
    doc[field] = digest
    return rfc8785.dumps(doc), digest


def _object(raw: bytes | str) -> dict[str, Any]:
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise DafterError(ErrorCode.INVALID_CONFIG, f"input is not valid JSON: {exc}") from exc
    if not isinstance(doc, dict):
        raise DafterError(ErrorCode.INVALID_CONFIG, "document is not a JSON object")
    return doc
