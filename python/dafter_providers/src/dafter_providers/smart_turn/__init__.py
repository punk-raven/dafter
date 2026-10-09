from __future__ import annotations

import functools
import importlib.util
import logging
import os
from pathlib import Path

from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError, ProviderContext

from .detector import MODEL, OnnxEndpointModel, SmartTurnDetector
from .weights import REVISION, WEIGHTS_FILE, fetch, weights_path

NAME = "smart_turn"
DETECTOR_AT = "/turn/detector"
PREWARM_ENV = "DAFTER_SMART_TURN_PREWARM"
PREWARM_OPTED_IN = "1"
RUNTIMES = ("onnxruntime", "transformers")
LANGUAGES = frozenset(
    {
        "ar",
        "bn",
        "da",
        "de",
        "en",
        "es",
        "fi",
        "fr",
        "hi",
        "id",
        "it",
        "ja",
        "ko",
        "mr",
        "nl",
        "no",
        "pl",
        "pt",
        "ru",
        "tr",
        "uk",
        "vi",
        "zh",
    }
)

log = logging.getLogger("dafter.providers.smart_turn")


def _installed(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        return False


def missing() -> str | None:
    absent = [module for module in RUNTIMES if not _installed(module)]
    if absent:
        return f"{', '.join(absent)} not installed in this worker"
    if not weights_path().is_file():
        return f"{WEIGHTS_FILE} at revision {REVISION} not fetched into {weights_path().parent}"
    return None


@functools.cache
def _model(weights: Path) -> OnnxEndpointModel:
    return OnnxEndpointModel(weights)


def prewarm() -> None:
    if os.environ.get(PREWARM_ENV) == PREWARM_OPTED_IN:
        try:
            fetch()
        except Exception as exc:
            log.warning(
                "smart turn weights not fetched; sessions that choose it are refused",
                extra={"error": type(exc).__name__},
            )
            return
    if missing() is not None:
        return
    try:
        _model(weights_path())
    except Exception as exc:
        log.warning(
            "smart turn model not loaded; it loads on the first session that chooses it",
            extra={"error": type(exc).__name__},
        )


def build_detector() -> SmartTurnDetector:
    if because := missing():
        raise DafterError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"the {NAME} turn detector cannot run in this worker",
            provider=ProviderContext(NAME),
            details=(f"at '{DETECTOR_AT}': {because}",),
        )
    return SmartTurnDetector(_model(weights_path()), LANGUAGES)


__all__ = ["LANGUAGES", "MODEL", "NAME", "build_detector", "missing", "prewarm"]
