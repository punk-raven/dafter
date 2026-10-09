from __future__ import annotations

import importlib
import importlib.util

from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError, ProviderContext
from livekit import rtc

NAME = "krisp"
PLUGIN = "livekit.plugins.noise_cancellation"
FILTER_AT = "/agent/pipeline/noiseFilter"
MODELS = {"nc": "NC", "bvc": "BVC", "bvc_telephony": "BVCTelephony"}
WIDEBAND_ONLY = frozenset({"bvc"})
PHONE_VARIANTS = {"nc": "nc", "bvc": "bvc_telephony", "bvc_telephony": "bvc_telephony"}


def installed() -> bool:
    try:
        return importlib.util.find_spec(PLUGIN) is not None
    except ModuleNotFoundError:
        return False


def _unavailable(message: str, because: str) -> DafterError:
    return DafterError(
        ErrorCode.UNSUPPORTED_CAPABILITY,
        message,
        provider=ProviderContext(NAME),
        details=(f"at '{FILTER_AT}': {because}",),
    )


def build_filter(name: str) -> rtc.NoiseCancellationOptions:
    model = MODELS.get(name)
    if model is None:
        raise _unavailable(
            f"{NAME} runs no noise filter of that name",
            f"one of {', '.join(sorted(MODELS))}",
        )
    try:
        plugin = importlib.import_module(PLUGIN)
    except ImportError as exc:
        raise _unavailable(
            f"the {NAME} noise filter plugin is not installed in this worker",
            f"{name} needs {PLUGIN}",
        ) from exc
    made: rtc.NoiseCancellationOptions = getattr(plugin, model)()
    return made
