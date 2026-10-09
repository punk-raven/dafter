from __future__ import annotations

import importlib.machinery
import sys
import types

import pytest
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError
from dafter_providers import NOISE_FILTERS, krisp, noise_filter_for
from livekit import rtc


@pytest.fixture
def plugin(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = types.ModuleType(krisp.PLUGIN)
    module.__spec__ = importlib.machinery.ModuleSpec(krisp.PLUGIN, None)
    for model in krisp.MODELS.values():
        setattr(module, model, lambda model=model: rtc.NoiseCancellationOptions(model, {}))
    monkeypatch.setitem(sys.modules, krisp.PLUGIN, module)
    return module


@pytest.fixture
def no_plugin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, krisp.PLUGIN, None)


def test_off_registers_no_filter_and_each_model_is_registered() -> None:
    assert noise_filter_for("off") is None
    assert set(NOISE_FILTERS) == {"nc", "bvc", "bvc_telephony"}
    assert {f.vendor for f in NOISE_FILTERS.values()} == {krisp.NAME}


def test_only_bvc_needs_wideband_audio_and_each_filter_has_a_phone_variant() -> None:
    assert [n for n, f in NOISE_FILTERS.items() if not f.narrowband] == ["bvc"]
    assert {n: f.phone_variant for n, f in NOISE_FILTERS.items()} == {
        "nc": "nc",
        "bvc": "bvc_telephony",
        "bvc_telephony": "bvc_telephony",
    }
    for f in NOISE_FILTERS.values():
        assert NOISE_FILTERS[f.phone_variant].narrowband


def test_an_unregistered_filter_is_refused_at_its_pointer() -> None:
    with pytest.raises(DafterError) as exc:
        noise_filter_for("viva")
    assert exc.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert exc.value.details[0].startswith("at '/agent/pipeline/noiseFilter'")


@pytest.mark.usefixtures("plugin")
@pytest.mark.parametrize(
    ("name", "model"), [("nc", "NC"), ("bvc", "BVC"), ("bvc_telephony", "BVCTelephony")]
)
def test_each_filter_builds_its_plugin_model(name: str, model: str) -> None:
    assert krisp.installed()
    built = NOISE_FILTERS[name].build(name)
    assert built == rtc.NoiseCancellationOptions(model, {})


@pytest.mark.usefixtures("no_plugin")
def test_a_worker_without_the_plugin_reports_it_and_refuses_to_build() -> None:
    assert not krisp.installed()
    with pytest.raises(DafterError) as exc:
        krisp.build_filter("bvc")
    assert exc.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert exc.value.provider is not None and exc.value.provider.name == krisp.NAME
    assert krisp.PLUGIN in exc.value.details[0]
