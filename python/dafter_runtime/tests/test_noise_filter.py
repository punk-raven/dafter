from __future__ import annotations

import importlib.machinery
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError
from dafter_core.hashing import seal
from dafter_providers import krisp
from dafter_runtime.plan import Plan, load, plan
from dafter_runtime.stages import filtered, filtered_input
from livekit import rtc
from livekit.agents.voice.room_io.types import NoiseCancellationParams

AGENT = Path(__file__).resolve().parents[3] / "testdata" / "agent"
WEB_JOB = AGENT / "hindi-webrtc-job.json"
PHONE_JOB = AGENT / "hindi-telephony-job.json"
POOL = "dafter-py"


@pytest.fixture
def plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType(krisp.PLUGIN)
    module.__spec__ = importlib.machinery.ModuleSpec(krisp.PLUGIN, None)
    for model in krisp.MODELS.values():
        setattr(module, model, lambda model=model: rtc.NoiseCancellationOptions(model, {}))
    monkeypatch.setitem(sys.modules, krisp.PLUGIN, module)


@pytest.fixture
def no_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, krisp.PLUGIN, None)


def job(source: Path, noise_filter: str, **changes: Any) -> bytes:
    doc = json.loads(source.read_bytes())
    doc["agent"]["pipeline"]["noiseFilter"] = noise_filter
    doc["agent"]["addressing"]["mode"] = changes.get("addressing", "always")
    if "client" in changes:
        doc["media"]["audio"]["noiseCancellation"] = changes["client"]
    sealed, _ = seal(json.dumps(doc))
    return sealed


def planned(raw: bytes) -> Plan:
    return plan(load(raw), POOL)


def refused(raw: bytes) -> DafterError:
    with pytest.raises(DafterError) as caught:
        planned(raw)
    return caught.value


def heard_by(kind: rtc.ParticipantKind.ValueType, p: Plan) -> object:
    select = filtered(p)
    assert select is not None
    params = NoiseCancellationParams(
        participant=types.SimpleNamespace(kind=kind),  # type: ignore[arg-type]
        track=None,  # type: ignore[arg-type]
    )
    return select(params)


@pytest.mark.usefixtures("no_plugin")
def test_a_session_with_the_filter_off_hears_unfiltered_without_the_plugin() -> None:
    p = planned(job(WEB_JOB, "off"))
    assert p.noise_filter is None
    assert filtered(p) is None
    assert filtered_input(p, 16000).noise_cancellation is None


@pytest.mark.usefixtures("plugin")
def test_a_phone_guest_hears_through_the_narrowband_variant_of_the_filter() -> None:
    p = planned(job(WEB_JOB, "bvc"))
    assert p.noise_filter is not None and p.noise_filter.name == "bvc"
    standard = rtc.ParticipantKind.PARTICIPANT_KIND_STANDARD
    sip = rtc.ParticipantKind.PARTICIPANT_KIND_SIP
    assert heard_by(standard, p) == rtc.NoiseCancellationOptions("BVC", {})
    assert heard_by(sip, p) == rtc.NoiseCancellationOptions("BVCTelephony", {})
    assert filtered_input(p, 16000).sample_rate == 16000


@pytest.mark.usefixtures("plugin")
def test_the_wideband_filter_is_refused_on_a_phone_line() -> None:
    err = refused(job(PHONE_JOB, "bvc"))
    assert err.code is ErrorCode.INVALID_CONFIG
    assert err.details[0].startswith("at '/agent/pipeline/noiseFilter'")
    assert "bvc_telephony" in err.details[0]


@pytest.mark.usefixtures("plugin")
@pytest.mark.parametrize("name", ["nc", "bvc_telephony"])
def test_a_narrowband_filter_runs_on_a_phone_line(name: str) -> None:
    p = planned(job(PHONE_JOB, name))
    assert p.noise_filter is not None and p.noise_filter.name == name


@pytest.mark.usefixtures("plugin")
def test_a_filter_is_planned_where_the_agent_hears_through_listeners() -> None:
    p = planned(job(WEB_JOB, "nc", addressing="transcript"))
    assert p.called_by_name
    assert p.noise_filter is not None and p.noise_filter.name == "nc"


@pytest.mark.usefixtures("plugin")
@pytest.mark.parametrize("client", ["rnnoise", "rnnoise_gated"])
def test_a_filter_is_refused_behind_a_client_noise_model(client: str) -> None:
    err = refused(job(WEB_JOB, "nc", client=client))
    assert err.code is ErrorCode.INVALID_CONFIG
    assert err.details[0].startswith("at '/media/audio/noiseCancellation'")


@pytest.mark.usefixtures("plugin")
@pytest.mark.parametrize("client", ["off", "native"])
def test_a_filter_runs_beside_the_browser_suppression(client: str) -> None:
    assert planned(job(WEB_JOB, "nc", client=client)).noise_filter is not None


@pytest.mark.usefixtures("no_plugin")
def test_a_filter_is_refused_before_joining_when_the_plugin_is_missing() -> None:
    err = refused(job(WEB_JOB, "nc"))
    assert err.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert err.details[0].startswith("at '/agent/pipeline/noiseFilter'")
