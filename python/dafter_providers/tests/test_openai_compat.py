from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import openai
import pytest
from dafter_core.config import ProviderRef
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError
from dafter_providers import VENDORS, openai_compat, vendor_for
from dafter_providers.openai_compat.client import CompatLLM
from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    function_tool,
    llm,
)

KEY_REF = "secret://tenants/t_9c21a4be/gemini/api-key"
GOOGLE = "https://generativelanguage.googleapis.com/v1beta/openai/"
ENDPOINTS = [
    GOOGLE,
    "https://integrate.api.nvidia.com/v1",
    "https://api.groq.com/openai/v1",
    "https://openrouter.ai/api/v1",
    "https://api.openai.com/v1",
    "https://opencode.ai/zen/v1",
]
NO_RETRY = APIConnectOptions(max_retry=0)


@pytest.fixture(autouse=True)
def gemini_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-only-not-a-key")


def ref(model: str | None = "a-model", region: str | None = None, **options: Any) -> ProviderRef:
    return ProviderRef(
        provider="openai_compat",
        model=model,
        region=region,
        credential_ref=KEY_REF,
        options={"baseUrl": GOOGLE, **options},
    )


def built(r: ProviderRef) -> CompatLLM:
    made = openai_compat.build_llm(r)
    assert isinstance(made, CompatLLM)
    return made


@pytest.mark.parametrize("base_url", ENDPOINTS)
def test_one_vendor_speaks_to_every_endpoint_the_config_names(base_url: str) -> None:
    made = built(ref(baseUrl=base_url))
    assert str(made._client.base_url).rstrip("/") == base_url.rstrip("/")
    assert (made.model, made.provider) == ("a-model", "openai_compat")


def test_options_reach_the_request_settings() -> None:
    body = {"extra_body": {"google": {"thinking_config": {"thinking_level": "minimal"}}}}
    made = built(ref(temperature=0.2, maxTokens=120, reasoningEffort="minimal", extraBody=body))
    opts = made._opts
    assert (opts.temperature, opts.max_completion_tokens) == (0.2, 120)
    assert (opts.reasoning_effort, opts.extra_body) == ("minimal", body)


def test_an_unstated_effort_is_left_to_the_endpoint() -> None:
    assert not isinstance(built(ref())._opts.reasoning_effort, str)


def test_the_vendor_serves_llm_only_in_the_languages_it_declares() -> None:
    vendor = vendor_for(ref(), Stage.LLM)
    assert vendor.languages == frozenset({"hi", "hi-IN"})
    assert set(VENDORS) == {"sarvam", "openai_compat"}
    with pytest.raises(DafterError) as caught:
        vendor_for(ref(), Stage.STT)
    assert caught.value.code is ErrorCode.UNSUPPORTED_CAPABILITY


@pytest.mark.parametrize(
    ("r", "code", "pointer"),
    [
        (ref(model=None), ErrorCode.INVALID_CONFIG, "/agent/pipeline/llm/model"),
        (ref(region="ap-south-1"), ErrorCode.RESIDENCY_VIOLATION, "/agent/pipeline/llm/region"),
        (ref(baseUrl="http://x"), ErrorCode.INVALID_CONFIG, "/agent/pipeline/llm/options/baseUrl"),
        (
            ref(reasoningEffort="off"),
            ErrorCode.INVALID_CONFIG,
            "/agent/pipeline/llm/options/reasoningEffort",
        ),
        (ref(thinking=False), ErrorCode.INVALID_CONFIG, "/agent/pipeline/llm/options/thinking"),
        (ref(maxTokens="200"), ErrorCode.INVALID_CONFIG, "/agent/pipeline/llm/options/maxTokens"),
    ],
)
def test_bad_settings_fail_at_construction_located_by_pointer(
    r: ProviderRef, code: ErrorCode, pointer: str
) -> None:
    with pytest.raises(DafterError) as caught:
        openai_compat.build_llm(r)
    assert caught.value.code is code
    assert any(pointer in d for d in caught.value.details), caught.value.details


def test_a_missing_credential_names_its_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(DafterError) as caught:
        openai_compat.build_llm(
            ProviderRef(
                provider="openai_compat",
                model="m",
                credential_ref="secret://tenants/t_9c21a4be/openrouter/api-key",
                options={"baseUrl": ENDPOINTS[3]},
            )
        )
    assert caught.value.code is ErrorCode.AUTHENTICATION_FAILED
    assert "OPENROUTER_API_KEY" in caught.value.message


def sse(*contents: str) -> bytes:
    lines = [
        json.dumps(
            {
                "id": "c",
                "object": "chat.completion.chunk",
                "created": 0,
                "model": "a-model",
                "choices": [{"index": 0, "delta": {"content": c}, "finish_reason": None}],
            }
        )
        for c in contents
    ]
    return "".join(f"data: {line}\n\n" for line in [*lines, "[DONE]"]).encode()


def stubbed(status: int, body: bytes, sent: list[dict[str, Any]]) -> CompatLLM:
    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        kind = "text/event-stream" if status == 200 else "application/json"
        return httpx.Response(status, content=body, headers={"content-type": kind})

    client = openai.AsyncClient(
        api_key="test-only-not-a-key",
        base_url=GOOGLE,
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    return CompatLLM(model="a-model", client=client, max_completion_tokens=50)


async def accept(raw_arguments: dict[str, object]) -> None:
    return None


lookup_order = function_tool(
    accept,
    raw_schema={
        "name": "lookup_order",
        "description": "Look up an order.",
        "parameters": {
            "type": "object",
            "properties": {"order_id": {"type": "string"}},
            "required": ["order_id"],
        },
    },
)


def ask(model: CompatLLM, tools: list[llm.Tool] | None = None) -> str:
    ctx = llm.ChatContext()
    ctx.add_message(role="user", content="नमस्ते")

    async def run() -> str:
        response = await model.chat(chat_ctx=ctx, tools=tools, conn_options=NO_RETRY).collect()
        await model.aclose()
        return response.text

    return asyncio.run(run())


def test_parallel_tool_calls_are_turned_off_only_when_tools_are_offered() -> None:
    sent: list[dict[str, Any]] = []
    assert ask(stubbed(200, sse("नमस्ते", "।"), sent)) == "नमस्ते।"
    ask(stubbed(200, sse("ठीक"), sent), tools=[lookup_order])
    assert "parallel_tool_calls" not in sent[0]
    assert sent[1]["parallel_tool_calls"] is False
    assert sent[0]["max_completion_tokens"] == 50


def failure(status: int, body: object) -> BaseException:
    with pytest.raises(APIStatusError) as caught:
        ask(stubbed(status, json.dumps(body).encode(), []))
    return caught.value


@pytest.mark.parametrize(
    ("status", "body", "code"),
    [
        (429, {"error": {"message": "slow down", "code": "slow_down"}}, ErrorCode.RATE_LIMITED),
        (429, [{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}], ErrorCode.RATE_LIMITED),
        (
            429,
            {"error": {"code": "credit_balance_exhausted", "type": "insufficient_quota"}},
            ErrorCode.QUOTA_EXCEEDED,
        ),
        (402, {"error": {"code": 402, "message": "credits"}}, ErrorCode.QUOTA_EXCEEDED),
        (401, {"error": {"message": "bad key"}}, ErrorCode.AUTHENTICATION_FAILED),
        (404, {"error": {"message": "no such model"}}, ErrorCode.INVALID_CONFIG),
        (503, {"error": {"message": "overloaded"}}, ErrorCode.PROVIDER_UNAVAILABLE),
    ],
)
def test_endpoint_refusals_map_to_the_taxonomy(status: int, body: object, code: ErrorCode) -> None:
    err = openai_compat.classify(failure(status, body), Stage.LLM)
    assert err.code is code
    assert err.provider is not None and err.provider.native_code == str(status)
    assert "slow down" not in err.message


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (APIStatusError("gateway", status_code=504), ErrorCode.PROVIDER_TIMEOUT, True),
        (APITimeoutError(), ErrorCode.PROVIDER_TIMEOUT, True),
        (APIConnectionError(), ErrorCode.PROVIDER_UNAVAILABLE, True),
        (RuntimeError("bug"), ErrorCode.INTERNAL, False),
    ],
)
def test_transport_failures_map_to_the_taxonomy(
    exc: BaseException, code: ErrorCode, retryable: bool
) -> None:
    err = openai_compat.classify(exc, Stage.LLM)
    assert (err.code, err.retryable) == (code, retryable)
