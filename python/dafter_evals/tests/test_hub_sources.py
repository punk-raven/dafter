from __future__ import annotations

import io
import json
import urllib.parse
import wave
from pathlib import Path
from typing import Any

import pytest
from dafter_evals import hub, indicvoices, loaders
from dafter_evals.corpus import Sample, digest
from dafter_evals.hub import Location


def wav(seconds: float, rate: int = 16000) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\0\0" * int(rate * seconds))
    return out.getvalue()


class Hub:
    def __init__(self, config: str, speaker_column: str, rows: list[dict[str, Any]]) -> None:
        self.config = config
        self.speaker_column = speaker_column
        self.rows = rows
        self.asked: list[dict[str, str]] = []

    def get(self, url: str) -> bytes:
        if url.startswith(hub.DATASETS):
            return json.dumps({"sha": "r1"}).encode()
        if url.startswith(hub.ROWS):
            q = dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))
            self.asked.append(q)
            assert q["config"] == self.config
            offset, length = int(q["offset"]), int(q["length"])
            items = [
                {"row_idx": i, "row": self.rows[i]}
                for i in range(offset, min(offset + length, len(self.rows)))
            ]
            return json.dumps({"rows": items, "num_rows_total": len(self.rows)}).encode()
        return wav(0.05 * (1 + int(url.rsplit("/", 1)[1].split(".")[0])))


def hub_rows(speaker_column: str) -> list[dict[str, Any]]:
    return [
        {
            "duration": 4.0 + i,
            "text": "" if i == 4 else f"वाक्य {i}",
            "gender": "female" if i % 2 else "male",
            speaker_column: f"s{i % 3}",
            "audio_filepath": [{"src": f"https://hub.invalid/{i}.wav", "type": "audio/wav"}],
        }
        for i in range(6)
    ]


INDICVOICES = {
    "dataset": "ai4bharat/IndicVoices",
    "revision": "r1",
    "split": "valid",
    "license": "CC-BY-4.0",
    "languages": {"hi": "hindi"},
}
LAHAJA = {
    "dataset": "ai4bharat/Lahaja",
    "revision": "r1",
    "license": "MIT",
    "languages": {"hi": "test"},
}


def test_a_hub_member_names_its_config_unless_it_is_the_default() -> None:
    assert Location("default", "test", 3).member == "test/3"
    assert Location("hindi", "valid", 3).member == "hindi/valid/3"
    assert Location.parse("test/3") == Location("default", "test", 3)
    assert Location.parse("hindi/valid/3") == Location("hindi", "valid", 3)


def test_an_indicvoices_sample_reads_the_language_config_one_clip_per_speaker() -> None:
    fake = Hub("hindi", "speaker_id", hub_rows("speaker_id"))
    sample, audio = indicvoices.pin_indicvoices(fake.get, INDICVOICES, "hi", 3)
    assert [c.member for c in sample.clips] == ["hindi/valid/1", "hindi/valid/0", "hindi/valid/5"]
    assert [c.id for c in sample.clips] == ["hindi-valid-1", "hindi-valid-0", "hindi-valid-5"]
    assert len({c.speaker for c in sample.clips}) == 3
    assert all(digest(audio[c.id]) == c.sha256 for c in sample.clips)
    assert all(q["split"] == "valid" for q in fake.asked)
    assert sample.source == {k: INDICVOICES[k] for k in ("dataset", "revision", "license")}
    assert hub.audio(fake.get, "ai4bharat/IndicVoices", "hindi/valid/5") == audio["hindi-valid-5"]
    assert Sample.read(sample.dumps()) == sample


def test_a_lahaja_sample_skips_rows_without_a_reference() -> None:
    rows = hub_rows("sp_id")
    fake = Hub("default", "sp_id", rows)
    sample, _ = indicvoices.pin_lahaja(fake.get, LAHAJA, "hi", 10)
    assert "test/4" not in [c.member for c in sample.clips]
    assert {c.speaker for c in sample.clips} == {"s0", "s1", "s2"}
    assert sample.dataset == "lahaja"


def test_a_hub_source_at_another_revision_is_refused() -> None:
    fake = Hub("default", "sp_id", hub_rows("sp_id"))
    with pytest.raises(ValueError, match="pinned"):
        indicvoices.pin_lahaja(fake.get, {**LAHAJA, "revision": "r0"}, "hi", 3)


def test_a_gated_source_needs_a_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(loaders.HF_TOKEN, raising=False)
    with pytest.raises(SystemExit, match="indicvoices is gated"):
        loaders.pin_sample("indicvoices", INDICVOICES, "hi", 3, Path("unused"))


def test_a_language_the_source_does_not_have_is_refused_before_any_request() -> None:
    with pytest.raises(SystemExit, match="lahaja has no kn-IN"):
        loaders.pin_sample("lahaja", LAHAJA, "kn-IN", 3, Path("unused"))


def test_every_pinned_source_has_a_loader() -> None:
    root = Path(__file__).resolve().parents[3] / "testdata" / "asr" / "sources.json"
    assert set(json.loads(root.read_text(encoding="utf-8"))) == set(loaders.DATASETS)
