from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from dafter_core.enums import AddressingMode, Channel
from dafter_core.hashing import seal
from dafter_runtime.personas import RECORDING_NOTICES, SCRIPTS
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.worker import room_options, stt_sample_rate

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-telephony-job.json"
WEB_JOB = JOB.with_name("hindi-webrtc-job.json")
POOL = "dafter-py"
CALLER = "p_4b81e0d7"
RECORDED, NOT_RECORDED = RECORDING_NOTICES["hi"]


def job() -> bytes:
    return JOB.read_bytes().strip()


def variant(change: Callable[[dict[str, Any]], None], source: Path = JOB) -> Plan:
    doc = json.loads(source.read_bytes())
    change(doc)
    sealed, _ = seal(json.dumps(doc))
    return plan(load(sealed), POOL)


def test_the_pinned_phone_call_answers_every_turn_on_the_narrowband_line() -> None:
    p = plan(load(job()), POOL)
    assert p.config.channel is Channel.TELEPHONY
    assert p.config.agent.addressing.mode is AddressingMode.ALWAYS
    assert not p.called_by_name
    assert p.opening == p.persona.greeting
    assert stt_sample_rate(p) == 8000
    assert p.pipeline.tts is not None and p.pipeline.tts.options["sampleRate"] == 8000
    options = room_options(p, 8000)
    assert options.audio_input is not False and not options.close_on_disconnect


def unrecorded(doc: dict[str, Any]) -> None:
    doc["recording"] = {"enabled": False}


def only_when_recorded(doc: dict[str, Any]) -> None:
    doc["telephony"] = {"recordingNotice": "when_recorded"}


def silent_unless_recorded(doc: dict[str, Any]) -> None:
    unrecorded(doc)
    only_when_recorded(doc)


@pytest.mark.parametrize(
    ("change", "said"),
    [
        (lambda doc: None, RECORDED),
        (unrecorded, NOT_RECORDED),
        (only_when_recorded, RECORDED),
        (silent_unless_recorded, None),
        (lambda doc: doc.update(language="en-IN"), RECORDING_NOTICES["en"][0]),
    ],
    ids=["recorded", "not recorded", "when recorded", "silent when not recorded", "english"],
)
def test_the_caller_hears_whether_the_call_is_recorded(
    change: Callable[[dict[str, Any]], None], said: str | None
) -> None:
    doc = json.loads(job())
    change(doc)
    if doc["language"] == "en-IN":
        doc["turn"]["strategy"], doc["turn"]["localVadEnabled"] = "semantic", True
    sealed, _ = seal(json.dumps(doc))
    assert plan(load(sealed), POOL).disclosure == said


def test_a_webrtc_session_says_nothing_about_recording() -> None:
    p = variant(lambda doc: doc.update(recording={"enabled": False}), WEB_JOB)
    assert p.disclosure is None and not p.on_a_phone


def test_every_language_the_worker_speaks_has_its_recording_notice() -> None:
    assert {language for _, language in SCRIPTS} == set(RECORDING_NOTICES)
    for recorded, not_recorded in RECORDING_NOTICES.values():
        assert recorded and not_recorded and recorded != not_recorded


def takes_phone_guests(doc: dict[str, Any]) -> None:
    doc["telephony"] = {"trunk": "vobiz", "phoneGuests": "dial_out"}
    doc["recording"] = {"enabled": True, "layout": "track", "consentArtifactId": "consent_rec"}


def test_a_meeting_that_takes_phone_guests_keeps_its_own_tuning_and_tells_them() -> None:
    p = variant(takes_phone_guests, WEB_JOB)
    assert p.takes_phone_calls and not p.on_a_phone
    assert p.disclosure == RECORDED
    assert p.called_by_name and p.opening is None
    assert stt_sample_rate(p) == 16000


def always(doc: dict[str, Any]) -> None:
    doc["agent"]["addressing"]["mode"] = "always"


def test_an_always_meeting_stays_up_for_later_phones_only_when_it_takes_them() -> None:
    def always_with_phones(doc: dict[str, Any]) -> None:
        always(doc)
        takes_phone_guests(doc)

    assert not room_options(variant(always_with_phones, WEB_JOB), 24000).close_on_disconnect
    assert room_options(variant(always, WEB_JOB), 24000).close_on_disconnect
