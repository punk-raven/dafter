from __future__ import annotations

import io
import wave
from pathlib import Path

import numpy as np
import pytest
from dafter_evals.conditions import (
    QUIET_GAIN_DB,
    SIR_LEVELS_DB,
    SNR_LEVELS_DB,
    WORK_RATE,
    Audio,
    Condition,
    Sources,
    active_power,
    conditions_for,
    degrade,
    fit,
    load_bank,
    mix_at_ratio,
    mulaw_decode,
    mulaw_encode,
    nc_overrides,
    read_wav,
    resample,
    source_directory,
    through_line,
)


def tone(seconds: float = 1.0, rate: int = WORK_RATE, amplitude: float = 8000) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.int16)


def wav_bytes(samples: np.ndarray, rate: int, width: int = 2) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buffer.getvalue()


def ratio_db(primary: np.ndarray, mixed: np.ndarray, rate: int) -> float:
    residue = mixed.astype(np.float64) - primary.astype(np.float64)
    return float(10 * np.log10(active_power(primary, rate) / np.mean(residue**2)))


def test_the_condition_kinds_expand_to_every_level() -> None:
    names = [c.name for c in conditions_for(["clean", "snr", "sir", "quiet", "g711"])]
    assert names == [
        "clean",
        *(f"snr_{db}db" for db in SNR_LEVELS_DB),
        "sir_10db",
        "sir_5db",
        "sir_0db",
        "sir_m5db",
        "quiet",
        "g711",
    ]
    assert len(SIR_LEVELS_DB) == 4
    with pytest.raises(ValueError, match="unknown condition"):
        conditions_for(["reverb"])


def test_each_noise_filter_mode_names_one_agent_side_filter() -> None:
    assert nc_overrides("off") == {}
    assert nc_overrides("nb") == {"agent": {"pipeline": {"noiseFilter": "nc"}}}
    assert nc_overrides("bvc") == {"agent": {"pipeline": {"noiseFilter": "bvc_telephony"}}}
    with pytest.raises(ValueError, match="unknown noise filter"):
        nc_overrides("krisp")


def test_mulaw_round_trip_keeps_silence_and_tracks_the_signal() -> None:
    assert mulaw_decode(mulaw_encode(np.zeros(4, dtype=np.int16))).tolist() == [0, 0, 0, 0]
    ramp = np.linspace(-32000, 32000, 2001).astype(np.int16)
    decoded = mulaw_decode(mulaw_encode(ramp)).astype(np.int64)
    assert np.all(np.diff(decoded) >= 0)
    assert np.all(np.abs(decoded - ramp) <= np.maximum(16, np.abs(ramp.astype(np.int64)) // 16))
    assert mulaw_encode(np.array([0], dtype=np.int16)).tolist() == [0xFF]


def test_the_line_round_trip_keeps_length_and_drops_what_8k_cannot_carry() -> None:
    low = tone()
    lined = through_line(Audio("low", low, WORK_RATE))
    assert lined.samples.size == low.size
    assert active_power(lined.samples, WORK_RATE) > 0.5 * active_power(low, WORK_RATE)
    t = np.arange(WORK_RATE) / WORK_RATE
    high = (8000 * np.sin(2 * np.pi * 6000 * t)).astype(np.int16)
    gone = through_line(Audio("high", high, WORK_RATE))
    assert active_power(gone.samples, WORK_RATE) < 0.05 * active_power(high, WORK_RATE)


def test_resample_scales_the_length() -> None:
    assert resample(tone(rate=8000), 8000, 24000).size == 24000
    assert resample(tone(), WORK_RATE, WORK_RATE).size == WORK_RATE


@pytest.mark.parametrize("target_db", [20.0, 5.0, 0.0, -5.0])
def test_mixing_lands_on_the_asked_ratio(target_db: float) -> None:
    rng = np.random.default_rng(1)
    speech = tone(amplitude=3000)
    noise = rng.normal(0, 3000, WORK_RATE).astype(np.int16)
    mixed = mix_at_ratio(speech, noise, WORK_RATE, target_db)
    assert ratio_db(speech, mixed, WORK_RATE) == pytest.approx(target_db, abs=0.3)


def test_fit_loops_short_sources_and_cuts_long_ones() -> None:
    rng = np.random.default_rng(0)
    assert fit(np.arange(10, dtype=np.int16), 25, rng).size == 25
    assert fit(np.arange(100, dtype=np.int16), 25, rng).size == 25
    assert fit(np.zeros(0, dtype=np.int16), 5, rng).tolist() == [0] * 5


def test_degrade_mixes_a_source_other_than_the_clip_itself() -> None:
    rng = np.random.default_rng(3)
    speech = Audio("clip-1", tone(), WORK_RATE)
    other = Audio("clip-2", tone(amplitude=2000)[::-1].copy(), WORK_RATE)
    sources = Sources(noises=(), interferers=(speech, other))
    mixed = degrade(Condition("sir_0db", "sir", 0), speech, sources, rng)
    assert mixed.samples.size == speech.samples.size
    assert not np.array_equal(mixed.samples, speech.samples)
    with pytest.raises(ValueError, match="needs a noise or voice source"):
        degrade(Condition("snr_10db", "snr", 10), speech, sources, rng)


def test_clean_is_untouched_and_quiet_is_quieter() -> None:
    rng = np.random.default_rng(0)
    speech = Audio("clip", tone(), WORK_RATE)
    assert degrade(Condition("clean", "clean"), speech, Sources(), rng) is speech
    quiet = degrade(Condition("quiet", "quiet", QUIET_GAIN_DB), speech, Sources(), rng)
    drop = 10 * np.log10(
        active_power(quiet.samples, WORK_RATE) / active_power(speech.samples, WORK_RATE)
    )
    assert drop == pytest.approx(QUIET_GAIN_DB, abs=1.0)


def test_banks_load_every_wav_at_the_work_rate(tmp_path: Path) -> None:
    (tmp_path / "musan" / "noise").mkdir(parents=True)
    (tmp_path / "musan" / "noise" / "a.wav").write_bytes(wav_bytes(tone(rate=8000), 8000))
    (tmp_path / "b.wav").write_bytes(wav_bytes(tone(rate=44100), 44100))
    bank = load_bank(tmp_path)
    assert [a.name for a in bank] == ["b.wav", "musan/noise/a.wav"]
    assert all(a.rate == WORK_RATE and a.samples.size == WORK_RATE for a in bank)
    assert load_bank(None) == ()
    with pytest.raises(ValueError, match="not a directory"):
        load_bank(tmp_path / "missing")


def test_only_16_bit_audio_is_read(tmp_path: Path) -> None:
    path = tmp_path / "wide.wav"
    path.write_bytes(wav_bytes(np.zeros(8, dtype=np.int32), 8000, width=4))
    with pytest.raises(ValueError, match="16-bit"):
        read_wav(path)


def test_a_source_directory_comes_from_the_flag_then_the_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DAFTER_NOISE_DIR", str(tmp_path))
    assert source_directory(None, "DAFTER_NOISE_DIR") == tmp_path
    assert source_directory(Path("given"), "DAFTER_NOISE_DIR") == Path("given")
    monkeypatch.delenv("DAFTER_NOISE_DIR")
    assert source_directory(None, "DAFTER_NOISE_DIR") is None
