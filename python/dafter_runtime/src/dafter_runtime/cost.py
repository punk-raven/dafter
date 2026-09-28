from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from importlib import resources
from typing import Any

from dafter_core.enums import Stage, UsageUnit
from livekit.agents.metrics import AgentSessionUsage, LLMModelUsage, STTModelUsage, TTSModelUsage

PRICES = "prices.csv"
COLUMNS = ["provider", "model", "unit", "inr", "per", "source", "as_of"]
PROVIDER = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
UNKNOWN = "unknown"
MODEL_MAX = 128
COST_PLACES = Decimal("0.000001")

Key = tuple[str, str, UsageUnit]
UNITS: dict[Stage, tuple[UsageUnit, ...]] = {
    Stage.STT: (UsageUnit.AUDIO_SECOND,),
    Stage.LLM: (UsageUnit.INPUT_TOKEN, UsageUnit.OUTPUT_TOKEN),
    Stage.TTS: (UsageUnit.CHARACTER,),
}


@dataclass(frozen=True, slots=True)
class Price:
    inr: Decimal
    per: Decimal
    source: str
    as_of: date

    def cost(self, quantity: float) -> Decimal:
        return (Decimal(str(quantity)) * self.inr / self.per).quantize(COST_PLACES)


@dataclass(frozen=True, slots=True)
class Item:
    stage: Stage
    provider: str
    model: str
    unit: UsageUnit
    quantity: float
    cost: Decimal | None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "stage": str(self.stage),
            "provider": self.provider,
            "model": self.model,
            "unit": str(self.unit),
            "quantity": self.quantity,
            "priced": self.cost is not None,
        }
        if self.cost is not None:
            d["costInr"] = float(self.cost)
        return d


def _row(n: int, row: dict[str, str]) -> tuple[Key, Price]:
    try:
        key = (row["provider"], row["model"], UsageUnit(row["unit"]))
        price = Price(
            inr=Decimal(row["inr"]),
            per=Decimal(row["per"]),
            source=row["source"],
            as_of=date.fromisoformat(row["as_of"]),
        )
    except (KeyError, ValueError, InvalidOperation) as exc:
        raise ValueError(f"price table row {n}: {exc}") from exc
    if price.inr < 0 or price.per <= 0 or not price.source.startswith("https://"):
        raise ValueError(f"price table row {n}: needs inr >= 0, per > 0 and an https source")
    return key, price


def load_prices(text: str | None = None) -> dict[Key, Price]:
    if text is None:
        text = resources.files("dafter_runtime").joinpath(PRICES).read_text(encoding="utf-8")
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames != COLUMNS:
        raise ValueError(f"price table columns must be {', '.join(COLUMNS)}")
    table: dict[Key, Price] = {}
    for n, row in enumerate(reader, start=2):
        key, price = _row(n, row)
        if key in table:
            raise ValueError(f"price table row {n}: a second price for {key}")
        table[key] = price
    return table


def provider_name(vendor: str) -> str:
    name = re.sub(r"[^a-z0-9_]", "_", vendor.strip().lower())
    return name if PROVIDER.match(name) else UNKNOWN


def model_name(model: str) -> str:
    return model.strip()[:MODEL_MAX] or UNKNOWN


def quantities(usage: AgentSessionUsage) -> list[tuple[Stage, str, str, UsageUnit, float]]:
    rows: list[tuple[Stage, str, str, UsageUnit, float]] = []
    for u in usage.model_usage:
        if isinstance(u, STTModelUsage):
            measured = [(Stage.STT, UsageUnit.AUDIO_SECOND, u.audio_duration)]
        elif isinstance(u, LLMModelUsage):
            measured = [
                (Stage.LLM, UsageUnit.INPUT_TOKEN, float(u.input_tokens)),
                (Stage.LLM, UsageUnit.OUTPUT_TOKEN, float(u.output_tokens)),
            ]
        elif isinstance(u, TTSModelUsage):
            measured = [(Stage.TTS, UsageUnit.CHARACTER, float(u.characters_count))]
        else:
            continue
        provider, model = provider_name(u.provider), model_name(u.model)
        rows.extend((stage, provider, model, unit, q) for stage, unit, q in measured)
    return rows


def priced(usage: AgentSessionUsage, table: dict[Key, Price]) -> list[Item]:
    items: list[Item] = []
    for stage, provider, model, unit, quantity in quantities(usage):
        price = table.get((provider, model, unit))
        cost = price.cost(quantity) if price is not None else None
        items.append(Item(stage, provider, model, unit, round(quantity, 3), cost))
    return items


def usage_payload(items: list[Item], final: bool) -> dict[str, Any]:
    total = sum((i.cost for i in items if i.cost is not None), Decimal(0))
    return {
        "final": final,
        "costInr": float(total),
        "unpricedItems": sum(1 for i in items if i.cost is None),
        "items": [i.to_dict() for i in items],
    }


class OutputTokens:
    def __init__(self) -> None:
        self._seen = 0.0

    def turn(self, usage: dict[str, Any]) -> int:
        total = float(
            sum(i["quantity"] for i in usage["items"] if i["unit"] == str(UsageUnit.OUTPUT_TOKEN))
        )
        spent, self._seen = total - self._seen, total
        return round(spent)
