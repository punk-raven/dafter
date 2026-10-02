from __future__ import annotations

from dafter_core.config import ProviderRef
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError, ProviderContext
from livekit.agents import inference, vad

from ..options import Options

NAME = "silero"
MODELS = frozenset({"silero"})
OPTIONS = ("minSpeechMs", "minSilenceMs", "prefixPaddingMs", "activationThreshold")


def _milliseconds(opts: Options, key: str, default: int) -> float:
    value = opts.get(key, int, default)
    if value < 0:
        raise opts.error("an option is out of range", f"at '{opts.pointer(key)}': at least 0")
    return value / 1000


def build_vad(ref: ProviderRef) -> vad.VAD:
    if ref.model is not None and ref.model not in MODELS:
        raise DafterError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            f"{NAME} vad is pinned to a model this worker does not run",
            stage=Stage.VAD,
            provider=ProviderContext(NAME),
            details=(f"at '/agent/pipeline/vad/model': one of {', '.join(sorted(MODELS))}",),
        )
    opts = Options(Stage.VAD, NAME, ref.options, OPTIONS)
    threshold = opts.get("activationThreshold", float, 0.5)
    if not 0 < threshold < 1:
        raise opts.error(
            "an option is out of range",
            f"at '{opts.pointer('activationThreshold')}': between 0 and 1, exclusive",
        )
    return inference.VAD(
        model="silero",
        min_speech_duration=_milliseconds(opts, "minSpeechMs", 50),
        min_silence_duration=_milliseconds(opts, "minSilenceMs", 250),
        prefix_padding_duration=_milliseconds(opts, "prefixPaddingMs", 500),
        activation_threshold=threshold,
    )


def classify(exc: BaseException, stage: Stage) -> DafterError:
    return DafterError(
        ErrorCode.INTERNAL,
        f"{NAME} {stage} failed ({type(exc).__name__})",
        stage=stage,
        provider=ProviderContext(NAME),
    )
