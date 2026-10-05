from __future__ import annotations

from typing import Any

import pytest
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError
from dafter_scribe.plan import AGENT_LABEL, language_of, plan
from dafter_scribe.worker import PERMISSIONS
from scribe_stub import config

SARVAM = {
    "provider": "sarvam",
    "model": "sarvam-105b",
    "region": "ap-south-1",
    "credentialRef": "secret://tenants/t_9c21a4be/sarvam/api-key",
}


def refused(pointer: str, **changes: Any) -> DafterError:
    with pytest.raises(DafterError) as caught:
        plan(config(**changes), "dafter-scribe")
    assert any(pointer in d for d in caught.value.details), caught.value.details
    return caught.value


def test_the_catalog_job_plans_a_sarvam_scribe_with_a_judge() -> None:
    p = plan(config(), "dafter-scribe")
    assert p.writer.source() == {"provider": "sarvam", "model": "sarvam-105b"}
    assert p.judge is not None and p.judge.at == "/scribe/judge"
    assert (p.interval_s, p.after_call_s) == (60.0, 900.0)
    assert (p.language.name, p.language.script) == ("Hindi", "Devanagari")
    assert p.agent_label == "Nivya"


def test_the_minutes_label_an_unnamed_agent_as_the_agent() -> None:
    p = plan(config(agent={"name": None, "addressing": {"mode": "always"}}), "dafter-scribe")
    assert p.agent_label == AGENT_LABEL


def test_a_scribe_without_a_judge_scores_nothing() -> None:
    p = plan(config(scribe={"judge": None, "summaryIntervalMs": 30000}), "dafter-scribe")
    assert p.judge is None and p.interval_s == 30.0


def test_a_job_for_another_pool_is_refused_before_joining() -> None:
    with pytest.raises(DafterError) as caught:
        plan(config(), "dafter-notes")
    assert caught.value.code is ErrorCode.INVALID_CONFIG
    assert "/scribe/pool" in caught.value.details[0]


def test_a_job_for_a_session_without_a_scribe_is_refused() -> None:
    refused("/scribe/enabled", scribe={"enabled": False})


def test_an_unregistered_scribe_llm_is_located_at_the_scribe() -> None:
    err = refused("/scribe/llm/provider", scribe={"llm": {"provider": "deepgram", "model": "x"}})
    assert err.code is ErrorCode.UNSUPPORTED_CAPABILITY


def test_an_llm_that_does_not_declare_the_language_is_refused() -> None:
    compat = {"provider": "google", "model": "m"}
    err = refused("/language", language="ta-IN", scribe={"llm": compat})
    assert err.code is ErrorCode.UNSUPPORTED_CAPABILITY


def test_an_encrypted_session_needs_the_control_plane_credential() -> None:
    trusted = {
        "privacyMode": "trusted_agent",
        "media": {"encryption": {"mode": "e2ee", "keyModel": "server_shared"}},
    }
    with pytest.raises(DafterError) as caught:
        plan(config(**trusted), "dafter-scribe")
    assert "/media/encryption/mode" in caught.value.details[0]
    assert plan(config(**trusted), "dafter-scribe", fetches_keys=True).config.privacy_mode


def test_languages_without_a_bank_are_named_by_their_tag() -> None:
    assert language_of("ta-IN").instruction().startswith("Write every field in ta-IN, ")
    assert language_of("en-IN").instruction().startswith("Write every field in English, ")
    assert "Hindi in Devanagari script" in language_of("hi").instruction()


def test_the_scribe_joins_hidden_and_can_publish_no_track() -> None:
    assert (
        PERMISSIONS.hidden and not PERMISSIONS.can_publish and not PERMISSIONS.can_publish_sources
    )
    assert PERMISSIONS.can_publish_data
