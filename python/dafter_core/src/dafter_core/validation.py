from __future__ import annotations

import json
from functools import cache
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

from . import schemas
from .enums import ErrorCode
from .errors import DafterError

_FILES = (schemas.IDS, schemas.RESOLVED_SESSION_CONFIG, schemas.EVENT_ENVELOPE, schemas.ERROR)


@cache
def _registry() -> Registry[Any]:
    resources = []
    for f in _FILES:
        doc = schemas.load(f)
        resources.append((doc["$id"], Resource.from_contents(doc)))
    return Registry().with_resources(resources)


@cache
def validator_for(relative: str) -> Draft202012Validator:
    return Draft202012Validator(
        schemas.load(relative), registry=_registry(), format_checker=FormatChecker()
    )


def _problems(validator: Draft202012Validator, doc: Any) -> tuple[str, ...]:
    seen: dict[str, None] = {}

    def walk(err: Any) -> None:
        if err.context:
            for sub in err.context:
                walk(sub)
            return
        seen.setdefault(f"at {err.json_path!r}: {err.message}", None)

    for err in validator.iter_errors(doc):
        walk(err)
    return tuple(sorted(seen))


def validate_document(relative: str, raw: bytes, code: ErrorCode) -> Any:
    """Validate raw JSON before it is decoded.

    Decoding first drops keys the target does not declare, so the schema never
    sees them.
    """
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise DafterError(ErrorCode.INVALID_CONFIG, f"input is not valid JSON: {exc}") from exc

    v = validator_for(relative)
    problems = _problems(v, doc)
    if problems:
        raise DafterError(
            code,
            f"{len(problems)} problem(s) validating against {schemas.schema_id(relative)}",
            details=problems,
        )
    return doc


def compile_all() -> None:
    for f in _FILES:
        validator_for(f).check_schema(schemas.load(f))


compile_all()
