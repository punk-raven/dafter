from __future__ import annotations

import io
import os
import wave
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

WORK_RATE = 16000
LINE_RATE = 8000
LEVEL_FRAME_MS = 10
SNR_LEVELS_DB = (20, 15, 10, 5, 0)
SIR_LEVELS_DB = (10, 5, 0, -5)
QUIET_GAIN_DB = -20
ACTIVE_RANGE_DB = 30.0
SILENT_FLOOR_DB = -60.0
CONDITION_KINDS = ("clean", "snr", "sir", "quiet", "g711")
NC_MODES = ("off", "nb", "bvc")
NC_FILTERS = {"nb": "nc", "bvc": "bvc_telephony"}
NOISE_DIR_ENV = "DAFTER_NOISE_DIR"
VOICES_DIR_ENV = "DAFTER_VOICES_DIR"
MULAW_BIAS = 0x84
MULAW_CLIP = 32635
LOWPASS_TAPS = 63
LOWPASS_FRACTION = 0.45


@dataclass(frozen=True, slots=True)
class Audio:
    name: str
    samples: np.ndarray
    rate: int


@dataclass(frozen=True, slots=True)
class Condition:
    name: str
    kind: str
    level_db: float | None = None


@dataclass(frozen=True, slots=True)
class Sources:
    noises: tuple[Audio, ...] = ()
    interferers: tuple[Audio, ...] = ()


def conditions_for(kinds: Iterable[str]) -> tuple[Condition, ...]:
    chosen: list[Condition] = []
    for kind in kinds:
        if kind not in CONDITION_KINDS:
            raise ValueError(f"unknown condition {kind!r}; one of {', '.join(CONDITION_KINDS)}")
        if kind == "snr":
            chosen.extend(Condition(f"snr_{_signed(db)}db", kind, db) for db in SNR_LEVELS_DB)
        elif kind == "sir":
            chosen.extend(Condition(f"sir_{_signed(db)}db", kind, db) for db in SIR_LEVELS_DB)
        elif kind == "quiet":
            chosen.append(Condition("quiet", kind, QUIET_GAIN_DB))
        else:
            chosen.append(Condition(kind, kind))
    return tuple(chosen)


def _signed(db: float) -> str:
    return f"m{abs(int(db))}" if db < 0 else str(int(db))


def nc_overrides(mode: str) -> dict[str, object]:
    if mode not in NC_MODES:
        raise ValueError(f"unknown noise filter {mode!r}; one of {', '.join(NC_MODES)}")
    if mode == "off":
        return {}
    return {"agent": {"pipeline": {"noiseFilter": NC_FILTERS[mode]}}}


def read_wav(path: Path, name: str | None = None) -> Audio:
    with wave.open(io.BytesIO(path.read_bytes())) as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path}: only 16-bit PCM audio is read")
        rate, channels = w.getframerate(), w.getnchannels()
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    mono = samples.reshape(-1, channels)[:, 0] if channels > 1 else samples
    return Audio(name or path.stem, mono.astype(np.int16), rate)


def resample(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if source_rate == target_rate or samples.size == 0:
        return samples.astype(np.int16)
    signal = samples.astype(np.float64)
    if target_rate < source_rate:
        signal = lowpass(signal, LOWPASS_FRACTION * target_rate / source_rate)
    count = max(1, round(signal.size * target_rate / source_rate))
    at = np.linspace(0, signal.size - 1, num=count)
    return to_pcm(np.interp(at, np.arange(signal.size), signal))


def lowpass(signal: np.ndarray, cutoff: float) -> np.ndarray:
    n = np.arange(LOWPASS_TAPS) - (LOWPASS_TAPS - 1) / 2
    taps = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hanning(LOWPASS_TAPS)
    taps /= taps.sum()
    filtered: np.ndarray = np.convolve(signal, taps, mode="same")
    return filtered


def to_pcm(signal: np.ndarray) -> np.ndarray:
    return np.clip(np.round(signal), -32768, 32767).astype(np.int16)


def mulaw_encode(samples: np.ndarray) -> np.ndarray:
    x = samples.astype(np.int32)
    sign = (x < 0).astype(np.int32)
    magnitude = np.minimum(np.abs(x), MULAW_CLIP) + MULAW_BIAS
    exponent = np.clip(np.floor(np.log2(magnitude)).astype(np.int32) - 7, 0, 7)
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    codes: np.ndarray = (~((sign << 7) | (exponent << 4) | mantissa) & 0xFF).astype(np.uint8)
    return codes


def mulaw_decode(codes: np.ndarray) -> np.ndarray:
    u = ~codes.astype(np.int32) & 0xFF
    exponent = (u >> 4) & 0x07
    magnitude = ((((u & 0x0F) << 3) + MULAW_BIAS) << exponent) - MULAW_BIAS
    return np.where(u & 0x80, -magnitude, magnitude).astype(np.int16)


def through_line(audio: Audio) -> Audio:
    narrow = resample(audio.samples, audio.rate, LINE_RATE)
    decoded = mulaw_decode(mulaw_encode(narrow))
    return Audio(audio.name, resample(decoded, LINE_RATE, audio.rate), audio.rate)


def frame_levels_db(samples: np.ndarray, rate: int) -> np.ndarray:
    size = max(1, rate * LEVEL_FRAME_MS // 1000)
    count = samples.size // size
    if count == 0:
        return np.zeros(0)
    frames = samples[: count * size].astype(np.float64).reshape(count, size) / 32768.0
    power = np.mean(frames * frames, axis=1)
    levels: np.ndarray = 10 * np.log10(np.maximum(power, 1e-12))
    return levels


def active_frames(samples: np.ndarray, rate: int) -> np.ndarray:
    levels = frame_levels_db(samples, rate)
    if levels.size == 0:
        return np.zeros(0, dtype=bool)
    floor = max(SILENT_FLOOR_DB, float(levels.max()) - ACTIVE_RANGE_DB)
    active: np.ndarray = levels >= floor
    return active


def active_power(samples: np.ndarray, rate: int) -> float:
    size = max(1, rate * LEVEL_FRAME_MS // 1000)
    active = active_frames(samples, rate)
    if not active.any():
        return 0.0
    frames = samples[: active.size * size].astype(np.float64).reshape(active.size, size)
    return float(np.mean(frames[active] ** 2))


def fit(other: np.ndarray, length: int, rng: np.random.Generator) -> np.ndarray:
    if other.size == 0:
        return np.zeros(length, dtype=np.int16)
    if other.size < length:
        other = np.tile(other, -(-length // other.size))
    start = int(rng.integers(0, other.size - length + 1))
    return other[start : start + length]


def mix_at_ratio(primary: np.ndarray, other: np.ndarray, rate: int, ratio_db: float) -> np.ndarray:
    primary_power = active_power(primary, rate)
    other_power = active_power(other, rate)
    if primary_power == 0 or other_power == 0:
        return primary.astype(np.int16)
    gain = np.sqrt(primary_power / (other_power * 10 ** (ratio_db / 10)))
    return to_pcm(primary.astype(np.float64) + gain * other.astype(np.float64))


def pick(bank: tuple[Audio, ...], excluded: str, rng: np.random.Generator) -> Audio:
    choices = [a for a in bank if a.name != excluded] or list(bank)
    if not choices:
        raise ValueError("the condition needs a noise or voice source and none was given")
    return choices[int(rng.integers(0, len(choices)))]


def degrade(
    condition: Condition, speech: Audio, sources: Sources, rng: np.random.Generator
) -> Audio:
    if condition.kind == "clean":
        return speech
    if condition.kind == "g711":
        return through_line(speech)
    if condition.kind == "quiet":
        gain = 10 ** ((condition.level_db or 0) / 20)
        quieter = to_pcm(speech.samples.astype(np.float64) * gain)
        return through_line(Audio(speech.name, quieter, speech.rate))
    bank = sources.noises if condition.kind == "snr" else sources.interferers
    other = pick(bank, speech.name, rng)
    if other.rate != speech.rate:
        raise ValueError(f"{other.name} is at {other.rate} Hz, the speech at {speech.rate} Hz")
    backdrop = fit(other.samples, speech.samples.size, rng)
    mixed = mix_at_ratio(speech.samples, backdrop, speech.rate, condition.level_db or 0)
    return through_line(Audio(speech.name, mixed, speech.rate))


def source_directory(given: Path | None, env: str) -> Path | None:
    if given is not None:
        return given
    named = os.environ.get(env, "").strip()
    return Path(named) if named else None


def load_bank(directory: Path | None, rate: int = WORK_RATE) -> tuple[Audio, ...]:
    if directory is None:
        return ()
    if not directory.is_dir():
        raise ValueError(f"{directory}: not a directory of wav files")
    bank = []
    for path in sorted(directory.rglob("*.wav")):
        audio = read_wav(path, str(path.relative_to(directory)))
        bank.append(Audio(audio.name, resample(audio.samples, audio.rate, rate), rate))
    return tuple(bank)


def at_rate(audio: Audio, rate: int) -> Audio:
    return Audio(audio.name, resample(audio.samples, audio.rate, rate), rate)
