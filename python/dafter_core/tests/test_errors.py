from __future__ import annotations

import json

import pytest
from dafter_core.config import parse
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError, ProviderContext
from dafter_core.validation import validate_document

from dafter_core import schemas


def test_error_serializes_within_its_own_schema() -> None:
    with pytest.raises(DafterError) as exc:
        parse('{"apiVersion":"wrong"}')
    raw = json.dumps(exc.value.to_dict()).encode()
    validate_document(schemas.ERROR, raw, ErrorCode.INTERNAL)


def test_wire_form_uses_schema_field_names() -> None:
    e = DafterError(
        ErrorCode.PROVIDER_TIMEOUT,
        "slow",
        stage=Stage.TTS,
        provider=ProviderContext("sarvam", request_id="req_1", native_code="1013"),
        details=("at '': bad",),
    )
    d = e.to_dict()
    assert d == {
        "code": "provider_timeout",
        "message": "slow",
        "retryable": True,
        "stage": "tts",
        "provider": {"name": "sarvam", "requestId": "req_1", "nativeCode": "1013"},
        "details": ["at '': bad"],
    }
    validate_document(schemas.ERROR, json.dumps(d).encode(), ErrorCode.INTERNAL)


def test_optional_fields_are_omitted_not_null() -> None:
    d = DafterError(ErrorCode.INTERNAL, "boom").to_dict()
    assert set(d) == {"code", "message", "retryable"}
    validate_document(schemas.ERROR, json.dumps(d).encode(), ErrorCode.INTERNAL)


def test_message_names_the_schema_by_id() -> None:
    with pytest.raises(DafterError) as exc:
        parse('{"apiVersion":"wrong"}')
    assert exc.value.message.endswith(schemas.schema_id(schemas.RESOLVED_SESSION_CONFIG))
