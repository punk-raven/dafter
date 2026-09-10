from __future__ import annotations

import json
from typing import Any

import pytest
from dafter_core.config import parse
from dafter_core.enums import Channel, EgressLayout, ErrorCode, PrivacyMode, TurnStrategy
from dafter_core.errors import DafterError

MINIMAL: dict[str, Any] = {
    "apiVersion": "dafter.dev/v1",
    "sessionId": "s_7f3a9c21",
    "tenantId": "t_9c21a4be",
    "privacyMode": "open",
    "language": "en-IN",
    "channel": "webrtc",
    "agent": {"enabled": True, "pool": "dafter-py"},
    "turn": {"strategy": "auto"},
    "recording": {"enabled": False},
    "budgets": {"turnGapP50Ms": 800, "turnGapP95Ms": 1500},
}


def doc(**overrides: Any) -> str:
    d = json.loads(json.dumps(MINIMAL))
    d.update(overrides)
    return json.dumps(d)


def refuse(raw: str) -> DafterError:
    with pytest.raises(DafterError) as exc:
        parse(raw)
    return exc.value


def test_parses_a_minimal_document() -> None:
    c = parse(doc())
    assert c.session_id == "s_7f3a9c21"
    assert c.agent.pool == "dafter-py"
    assert c.channel is Channel.WEBRTC
    assert c.turn.strategy is TurnStrategy.AUTO
    assert c.turn.local_vad_enabled is True


def test_defaults_come_from_the_schema_not_the_dataclass() -> None:
    c = parse(doc(recording={"enabled": True, "consentArtifactId": "consent_1"}))
    assert c.recording.layout is EgressLayout.TRACK


def test_rejects_a_misspelled_key() -> None:
    d = json.loads(json.dumps(MINIMAL))
    d["privacymode"] = d.pop("privacyMode")
    err = refuse(json.dumps(d))
    assert err.code is ErrorCode.INVALID_CONFIG
    assert any("privacymode" in p for p in err.details)


def test_rejects_an_inline_secret() -> None:
    err = refuse(
        doc(
            agent={
                "enabled": True,
                "pool": "dafter-py",
                "pipeline": {"stt": {"provider": "sarvam", "credentialRef": "sk-live-abc"}},
            }
        )
    )
    assert any("credentialRef" in p for p in err.details)


def test_rejects_a_value_the_dataclass_would_accept() -> None:
    err = refuse(doc(agent={"enabled": True, "pool": "NOT A VALID POOL NAME"}))
    assert any("agent.pool" in p for p in err.details)


def test_rejects_a_non_opaque_session_id() -> None:
    err = refuse(doc(sessionId="call-with-jane@example.com"))
    assert any("sessionId" in p for p in err.details)


def test_rejects_malformed_json() -> None:
    assert refuse('{"apiVersion":').code is ErrorCode.INVALID_CONFIG


def test_reports_every_problem_at_once() -> None:
    err = refuse(doc(apiVersion="wrong", sessionId="nope", channel="carrier-pigeon", budgets={}))
    joined = "\n".join(err.details)
    for field in ("apiVersion", "sessionId", "channel", "turnGapP50Ms"):
        assert field in joined, f"{field} not named in:\n{joined}"


def test_sealed_session_refuses_an_agent() -> None:
    err = refuse(doc(privacyMode="sealed"))
    assert err.code is ErrorCode.PRIVACY_MODE_FORBIDS
    assert parse(doc(privacyMode="sealed", agent={"enabled": False, "pool": "dafter-py"}))


def test_recording_requires_consent() -> None:
    assert refuse(doc(recording={"enabled": True})).code is ErrorCode.CONSENT_REQUIRED


def test_track_egress_cannot_start_before_a_track_exists() -> None:
    err = refuse(
        doc(
            recording={
                "enabled": True,
                "layout": "track",
                "startAt": "session_create",
                "consentArtifactId": "consent_1",
            }
        )
    )
    assert err.code is ErrorCode.INVALID_CONFIG
    assert (
        parse(
            doc(
                recording={
                    "enabled": True,
                    "layout": "room_composite",
                    "startAt": "session_create",
                    "consentArtifactId": "consent_1",
                }
            )
        ).privacy_mode
        is PrivacyMode.OPEN
    )


def test_auth_failures_are_not_retryable() -> None:
    assert not DafterError(ErrorCode.AUTHENTICATION_FAILED, "bad key").retryable
    assert DafterError(ErrorCode.PROVIDER_TIMEOUT, "slow").retryable
