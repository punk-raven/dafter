from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from dafter_core.enums import EventType, UsageUnit
from dafter_core.events import parse_event
from dafter_runtime.cost import load_prices, priced, provider_name, usage_payload
from dafter_runtime.events import SessionEvents
from dafter_runtime.plan import load
from livekit.agents.metrics import (
    AgentSessionUsage,
    EOTModelUsage,
    LLMModelUsage,
    STTModelUsage,
    TTSModelUsage,
)

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
HEADER = "provider,model,unit,inr,per,source,as_of\n"
TABLE = HEADER + (
    "sarvam,saaras:v3-realtime,audio_second,30.00,3600,https://example.test/p,2026-09-27\n"
    "sarvam,sarvam-105b,input_token,29.28,1000000,https://example.test/p,2026-09-27\n"
    "sarvam,sarvam-105b,output_token,73.20,1000000,https://example.test/p,2026-09-27\n"
)


def sarvam_call() -> AgentSessionUsage:
    return AgentSessionUsage(
        model_usage=[
            LLMModelUsage(
                provider="Sarvam", model="sarvam-105b", input_tokens=1840, output_tokens=210
            ),
            TTSModelUsage(provider="Sarvam", model="bulbul:v3", characters_count=412),
            STTModelUsage(provider="Sarvam", model="saaras:v3-realtime", audio_duration=60.0),
            EOTModelUsage(provider="livekit", model="turn-detector", total_requests=4),
        ]
    )


def test_each_unit_is_priced_from_its_row_and_an_unknown_one_is_unpriced() -> None:
    payload = usage_payload(priced(sarvam_call(), load_prices(TABLE)), final=True)
    by_unit = {i["unit"]: i for i in payload["items"]}
    assert set(by_unit) == {"input_token", "output_token", "character", "audio_second"}
    assert by_unit["audio_second"] == {
        "stage": "stt",
        "provider": "sarvam",
        "model": "saaras:v3-realtime",
        "unit": "audio_second",
        "quantity": 60.0,
        "priced": True,
        "costInr": 0.5,
    }
    assert by_unit["input_token"]["costInr"] == 0.053875
    assert by_unit["output_token"]["costInr"] == 0.015372
    assert by_unit["character"] == {
        "stage": "tts",
        "provider": "sarvam",
        "model": "bulbul:v3",
        "unit": "character",
        "quantity": 412.0,
        "priced": False,
    }
    assert payload["unpricedItems"] == 1
    assert payload["costInr"] == 0.569247
    assert payload["final"] is True


def test_one_model_heard_by_several_listeners_is_one_item() -> None:
    usage = AgentSessionUsage(
        model_usage=[
            STTModelUsage(provider="Sarvam", model="saaras:v3-realtime", audio_duration=60.0),
            STTModelUsage(provider="Sarvam", model="saaras:v3-realtime", audio_duration=30.0),
        ]
    )
    [item] = usage_payload(priced(usage, load_prices(TABLE)), final=False)["items"]
    assert (item["quantity"], item["costInr"]) == (90.0, 0.75)


def test_nothing_priced_is_a_zero_floor_with_every_item_unpriced() -> None:
    payload = usage_payload(priced(sarvam_call(), load_prices(HEADER)), final=False)
    assert payload["costInr"] == 0
    assert payload["unpricedItems"] == 4
    assert all("costInr" not in i and i["priced"] is False for i in payload["items"])


def test_the_payload_is_a_valid_session_usage_event() -> None:
    sent: list[bytes] = []

    async def publish(body: bytes) -> None:
        sent.append(body)

    async def run() -> None:
        events = SessionEvents(
            load(JOB.read_bytes().strip()),
            publish,
            clock=lambda: datetime(2026, 9, 24, 10, 0, tzinfo=UTC),
        )
        payload = usage_payload(priced(sarvam_call(), load_prices()), final=True)
        assert events.emit(EventType.SESSION_USAGE, payload)
        await events.drain()

    asyncio.run(run())
    [event] = [parse_event(body) for body in sent]
    assert event.type is EventType.SESSION_USAGE
    assert json.loads(sent[0])["payload"]["unpricedItems"] == 0


def test_the_shipped_table_prices_the_sarvam_pipeline() -> None:
    table = load_prices()
    for model, unit in [
        ("saaras:v3-realtime", UsageUnit.AUDIO_SECOND),
        ("sarvam-105b", UsageUnit.INPUT_TOKEN),
        ("sarvam-105b", UsageUnit.OUTPUT_TOKEN),
        ("bulbul:v3", UsageUnit.CHARACTER),
    ]:
        price = table[("sarvam", model, unit)]
        assert price.source.startswith("https://www.sarvam.ai/")
    assert table[("sarvam", "bulbul:v3", UsageUnit.CHARACTER)].cost(1000) == Decimal("3")


@pytest.mark.parametrize(
    ("row", "because"),
    [
        ("sarvam,x,minute,1,1,https://example.test,2026-09-27", "minute"),
        ("sarvam,x,character,1,0,https://example.test,2026-09-27", "per > 0"),
        ("sarvam,x,character,-1,1,https://example.test,2026-09-27", "inr >= 0"),
        ("sarvam,x,character,1,1,http://example.test,2026-09-27", "https"),
        ("sarvam,x,character,one,1,https://example.test,2026-09-27", "row 2"),
        ("sarvam,x,character,1,1,https://example.test,yesterday", "row 2"),
    ],
)
def test_a_malformed_row_is_refused(row: str, because: str) -> None:
    with pytest.raises(ValueError, match=because):
        load_prices(HEADER + row + "\n")


def test_a_duplicate_row_is_refused() -> None:
    row = "sarvam,x,character,1,1,https://example.test,2026-09-27\n"
    with pytest.raises(ValueError, match="second price"):
        load_prices(HEADER + row + row)


def test_other_columns_are_refused() -> None:
    with pytest.raises(ValueError, match="columns"):
        load_prices("provider,model,unit,price\n")


@pytest.mark.parametrize(
    ("vendor", "name"),
    [("Sarvam", "sarvam"), ("", "unknown"), ("livekit-inference", "livekit_inference")],
)
def test_vendor_names_become_schema_provider_names(vendor: str, name: str) -> None:
    assert provider_name(vendor) == name
