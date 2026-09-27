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
from dafter_providers import VENDORS, credentials, openai_compat, vendor_for
from dafter_providers.openai_compat import endpoints
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
NO_RETRY = APIConnectOptions(max_retry=0)


@pytest.fixture(autouse=True)
def provider_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for endpoint in openai_compat.ENDPOINTS.values():
        monkeypatch.setenv(endpoint.credential_env, "test-only-not-a-key")


def key_ref(env: str) -> str:
    return f"secret://tenants/t_9c21a4be/{env.removesuffix('_API_KEY').lower()}/api-key"


def ref(
    model: str | None = "a-model",
    region: str | None = None,
    credential_ref: str = KEY_REF,
    **options: Any,
) -> ProviderRef:
    return ProviderRef(
        provider="openai_compat",
        model=model,
        region=region,
        credential_ref=credential_ref,
        options={"endpoint": "google", **options},
    )


def built(r: ProviderRef) -> CompatLLM:
    made = openai_compat.build_llm(r)
    assert isinstance(made, CompatLLM)
    return made


@pytest.mark.parametrize("name", sorted(openai_compat.ENDPOINTS))
def test_each_named_endpoint_is_reached_with_the_key_it_is_bound_to(name: str) -> None:
    endpoint = openai_compat.ENDPOINTS[name]
    made = built(ref(credential_ref=key_ref(endpoint.credential_env), endpoint=name))
    assert str(made._client.base_url).rstrip("/") == endpoint.base_url.rstrip("/")
    assert made._client.api_key == "test-only-not-a-key"
    assert (made.model, made.provider) == ("a-model", "openai_compat")


def test_the_endpoint_table_binds_each_https_host_to_its_own_provider_key() -> None:
    table = openai_compat.ENDPOINTS
    assert set(table) == {"google", "openrouter", "opencode_zen", "openai"}
    assert table["google"].base_url == GOOGLE
    keys = [e.credential_env for e in table.values()]
    assert len(set(keys)) == len(keys)
    assert set(keys) <= credentials.PROVIDER_CREDENTIALS
    assert all(e.base_url.startswith("https://") for e in table.values())


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"credentialEnv": "DAFTER_WORKER_SECRET"}, "DAFTER_WORKER_SECRET"),
        ({"credentialEnv": "OPENAI_API_KEY"}, "more than one endpoint"),
        ({"baseUrl": "http://generativelanguage.googleapis.com/"}, "https"),
        ({"headers": {"x": "y"}}, "exactly"),
    ],
)
def test_an_endpoint_table_that_binds_badly_is_refused(
    change: dict[str, Any], message: str
) -> None:
    table = {
        "google": {"baseUrl": GOOGLE, "credentialEnv": "GEMINI_API_KEY", **change},
        "openai": {"baseUrl": "https://api.openai.com/v1", "credentialEnv": "OPENAI_API_KEY"},
    }
    with pytest.raises(ValueError, match=message):
        endpoints.parse(json.dumps(table))


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
        (ref(endpoint="nowhere"), ErrorCode.INVALID_CONFIG, "/agent/pipeline/llm/options/endpoint"),
        (
            ref(endpoint=["google"]),
            ErrorCode.INVALID_CONFIG,
            "/agent/pipeline/llm/options/endpoint",
        ),
        (ref(baseUrl=GOOGLE), ErrorCode.INVALID_CONFIG, "/agent/pipeline/llm/options/baseUrl"),
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
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(DafterError) as caught:
        openai_compat.build_llm(
            ref(credential_ref=key_ref("OPENROUTER_API_KEY"), endpoint="openrouter")
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


@pytest.mark.parametrize(
    "options",
    [{"baseUrl": "https://attacker.example/v1"}, {"endpoint": "https://attacker.example/v1"}],
)
def test_a_session_cannot_send_a_trusted_key_to_a_host_it_names(options: dict[str, Any]) -> None:
    with pytest.raises(DafterError) as caught:
        openai_compat.build_llm(
            ProviderRef(
                provider="openai_compat", model="m", credential_ref=KEY_REF, options=options
            )
        )
    assert caught.value.code is ErrorCode.INVALID_CONFIG


def test_a_named_endpoint_reads_only_the_key_it_is_bound_to() -> None:
    with pytest.raises(DafterError) as caught:
        openai_compat.build_llm(
            ProviderRef(
                provider="openai_compat",
                model="m",
                credential_ref=KEY_REF,
                options={"endpoint": "openrouter"},
            )
        )
    assert caught.value.code is ErrorCode.INVALID_CONFIG
    assert any("/agent/pipeline/llm/credentialRef" in d for d in caught.value.details)
