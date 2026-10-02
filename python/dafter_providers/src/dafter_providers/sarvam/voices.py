from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from dafter_core.enums import Situation

from ..options import Options

PACE = (0.5, 2.0)
TEMPERATURE = (0.01, 1.0)
STYLE_KEYS = frozenset({"pace", "temperature"})
SITUATIONS = frozenset(str(s) for s in Situation)


@dataclass(frozen=True, slots=True)
class Voice:
    pace: float
    temperature: float


def _setting(
    opts: Options, settings: Mapping[str, Any], key: str, default: float, at: str
) -> float:
    value = settings.get(key, default)
    pointer = f"{at}/{key}"
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise opts.error("an option has the wrong type", f"at '{pointer}': expected float")
    opts.within(float(value), PACE if key == "pace" else TEMPERATURE, pointer)
    return float(value)


def _style(opts: Options, name: str, settings: object, base: Voice) -> Voice:
    pointer = f"{opts.pointer('styles')}/{name}"
    if name not in SITUATIONS:
        raise opts.error(
            "a voice style names no situation",
            f"at '{pointer}': one of {', '.join(sorted(SITUATIONS))}",
        )
    if not isinstance(settings, dict) or not set(settings) <= STYLE_KEYS:
        raise opts.error(
            "a voice style sets something this provider does not vary",
            f"at '{pointer}': an object of {', '.join(sorted(STYLE_KEYS))}",
        )
    return Voice(
        pace=_setting(opts, settings, "pace", base.pace, pointer),
        temperature=_setting(opts, settings, "temperature", base.temperature, pointer),
    )


def voices(opts: Options) -> tuple[Voice, dict[str, Voice]]:
    base = Voice(
        pace=opts.ranged("pace", 1.0, PACE),
        temperature=opts.ranged("temperature", 0.6, TEMPERATURE),
    )
    styles = opts.get("styles", dict, {})
    return base, {name: _style(opts, name, s, base) for name, s in styles.items()}


__all__ = ["PACE", "TEMPERATURE", "Voice", "voices"]
