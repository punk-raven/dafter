from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import openai
import pytest
from dafter_core.config import ProviderRef
from dafter_providers import sarvam
from dafter_providers.sarvam.llm import SarvamLLM
from livekit.agents import APIConnectOptions, function_tool, llm

KEY_REF = "secret://tenants/t_9c21a4be/sarvam/api-key"
NOT_A_KEY = "test-only-not-a-key"


@pytest.fixture(autouse=True)
def sarvam_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SARVAM_API_KEY", NOT_A_KEY)


def sse(*chunks: dict[str, Any]) -> bytes:
    lines = [json.dumps(c) for c in chunks]
    return "".join(f"data: {line}\n\n" for line in [*lines, "[DONE]"]).encode()


def said(content: str) -> dict[str, Any]:
    return {
        "id": "c",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "sarvam-105b",
        "choices": [{"index": 0, "delta": {"content": content}, "finish_reason": None}],
    }


def stubbed(sent: list[dict[str, Any]], body: bytes) -> SarvamLLM:
    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, content=body, headers={"content-type": "text/event-stream"})

    client = openai.AsyncClient(
        api_key=NOT_A_KEY,
        base_url=sarvam.LLM_BASE_URL,
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    return SarvamLLM(model="sarvam-105b", api_key=NOT_A_KEY, client=client)


async def forecast(raw_arguments: dict[str, object]) -> None:
    return None


get_weather = function_tool(
    forecast,
    raw_schema={
        "name": "get_weather",
        "description": "Weather for a city.",
        "parameters": {"type": "object", "properties": {"city": {"type": "string"}}},
    },
)


def asked(model: SarvamLLM, tools: list[llm.Tool] | None = None) -> llm.CollectedResponse:
    ctx = llm.ChatContext()
    ctx.add_message(role="user", content="दिल्ली और मुंबई का मौसम बताइए।")

    async def run() -> llm.CollectedResponse:
        stream = model.chat(chat_ctx=ctx, tools=tools, conn_options=APIConnectOptions(max_retry=0))
        response = await stream.collect()
        await model.aclose()
        return response

    return asyncio.run(run())


def test_llm_asks_for_one_tool_call_at_a_time_only_when_tools_are_offered() -> None:
    ref = ProviderRef(provider="sarvam", model="sarvam-105b", credential_ref=KEY_REF)
    assert isinstance(sarvam.build_llm(ref), SarvamLLM)
    sent: list[dict[str, Any]] = []
    assert asked(stubbed(sent, sse(said("ठीक है।")))).text == "ठीक है।"
    asked(stubbed(sent, sse(said("ठीक है।"))), [get_weather])
    assert "parallel_tool_calls" not in sent[0]
    assert sent[1]["parallel_tool_calls"] is False
    assert sent[1]["tools"][0]["function"]["name"] == "get_weather"


def usage(**fields: Any) -> dict[str, Any]:
    counts = {"completion_tokens": 60, "prompt_tokens": 17, "total_tokens": 77}
    return {**said(""), "choices": [], "usage": {**counts, **fields}}


@pytest.mark.parametrize(
    ("fields", "reasoning"),
    [
        ({"completion_tokens_details": None, "reasoning_tokens": 60}, 60),
        ({"completion_tokens_details": {"reasoning_tokens": 12}, "reasoning_tokens": 60}, 12),
        ({"completion_tokens_details": None}, 0),
    ],
)
def test_the_reasoning_tokens_sarvam_reports_reach_the_usage(
    fields: dict[str, Any], reasoning: int
) -> None:
    response = asked(stubbed([], sse(said("ठीक है।"), usage(**fields))))
    assert response.usage is not None
    assert (response.usage.completion_tokens, response.usage.reasoning_tokens) == (60, reasoning)
    assert response.text == "ठीक है।"
