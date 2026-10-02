from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import httpx
import pytest
from dafter_core.config import ProviderRef
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError
from dafter_providers import VENDORS, credentials, openai_compat, vendor_for
from dafter_providers.openai_compat import endpoints
from dafter_providers.openai_compat.client import CompatLLM
from dafter_providers.openai_compat.wire import http_client
from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    function_tool,
    llm,
)

KEY_REF = "secret://tenants/t_9c21a4be/groq/api-key"
GROQ = "https://api.groq.com/openai/v1"
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
    provider: str = "groq",
    **options: Any,
) -> ProviderRef:
    return ProviderRef(
        provider=provider,
        model=model,
        region=region,
        credential_ref=credential_ref,
        options=options,
    )


def build(r: ProviderRef) -> llm.LLM[Any]:
    llm_for = VENDORS[r.provider].llm
    assert llm_for is not None
    return llm_for(r)


def built(r: ProviderRef) -> CompatLLM:
    made = build(r)
    assert isinstance(made, CompatLLM)
    return made


@pytest.mark.parametrize("name", sorted(openai_compat.ENDPOINTS))
def test_each_endpoint_is_a_vendor_reached_with_the_key_it_is_bound_to(name: str) -> None:
    endpoint = openai_compat.ENDPOINTS[name]
    made = built(ref(credential_ref=key_ref(endpoint.credential_env), provider=name))
    assert str(made._client.base_url).rstrip("/") == endpoint.base_url.rstrip("/")
    assert made._client.api_key == "test-only-not-a-key"
    assert (made.model, made.provider) == ("a-model", name)


def test_the_endpoint_table_binds_each_https_host_to_its_own_provider_key() -> None:
    table = openai_compat.ENDPOINTS
    assert set(table) == {"groq", "openrouter", "google", "opencode_zen", "openai"}
    assert table["groq"].base_url == GROQ
    assert table["openrouter"].base_url == "https://openrouter.ai/api/v1"
    assert table["google"].base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"
    assert table["google"].credential_env == "GEMINI_API_KEY"
    keys = [e.credential_env for e in table.values()]
    assert len(set(keys)) == len(keys)
    assert set(keys) <= credentials.PROVIDER_CREDENTIALS
    assert all(e.base_url.startswith("https://") for e in table.values())


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"credentialEnv": "DAFTER_WORKER_SECRET"}, "DAFTER_WORKER_SECRET"),
        ({"credentialEnv": "OPENROUTER_API_KEY"}, "more than one endpoint"),
        ({"baseUrl": "http://api.groq.com/openai/v1"}, "https"),
        ({"headers": {"x": "y"}}, "exactly"),
    ],
)
def test_an_endpoint_table_that_binds_badly_is_refused(
    change: dict[str, Any], message: str
) -> None:
    table = {
        "groq": {"baseUrl": GROQ, "credentialEnv": "GROQ_API_KEY", **change},
        "openrouter": {
            "baseUrl": "https://openrouter.ai/api/v1",
            "credentialEnv": "OPENROUTER_API_KEY",
        },
    }
    with pytest.raises(ValueError, match=message):
        endpoints.parse(json.dumps(table))


def test_an_endpoint_that_is_not_a_provider_name_is_refused() -> None:
    table = {"Groq Cloud": {"baseUrl": GROQ, "credentialEnv": "GROQ_API_KEY"}}
    with pytest.raises(ValueError, match="provider name"):
        endpoints.parse(json.dumps(table))


def test_options_reach_the_request_settings() -> None:
    body = {"reasoning": {"enabled": False}}
    made = built(ref(temperature=0.2, maxTokens=120, reasoningEffort="none", extraBody=body))
    opts = made._opts
    assert (opts.temperature, opts.max_completion_tokens) == (0.2, 120)
    assert (opts.reasoning_effort, opts.extra_body) == ("none", body)


def test_an_unstated_effort_is_left_to_the_endpoint() -> None:
    assert not isinstance(built(ref())._opts.reasoning_effort, str)


def table_names() -> set[str]:
    return set(openai_compat.ENDPOINTS)


def test_the_vendor_serves_llm_only_in_the_languages_it_declares() -> None:
    vendor = vendor_for(ref(), Stage.LLM)
    assert vendor.languages == frozenset({"hi", "hi-IN", "en-IN", "kn-IN", "mr-IN", "te-IN"})
    assert set(VENDORS) == {"sarvam", "silero", *table_names()}
    with pytest.raises(DafterError) as caught:
        vendor_for(ref(), Stage.STT)
    assert caught.value.code is ErrorCode.UNSUPPORTED_CAPABILITY


@pytest.mark.parametrize(
    ("r", "code", "pointer"),
    [
        (ref(model=None), ErrorCode.INVALID_CONFIG, "/agent/pipeline/llm/model"),
        (ref(region="ap-south-1"), ErrorCode.RESIDENCY_VIOLATION, "/agent/pipeline/llm/region"),
        (ref(endpoint="groq"), ErrorCode.INVALID_CONFIG, "/agent/pipeline/llm/options/endpoint"),
        (ref(baseUrl=GROQ), ErrorCode.INVALID_CONFIG, "/agent/pipeline/llm/options/baseUrl"),
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
        build(r)
    assert caught.value.code is code
    assert any(pointer in d for d in caught.value.details), caught.value.details


def test_a_missing_credential_names_its_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY")
    with pytest.raises(DafterError) as caught:
        build(ref(credential_ref=key_ref("OPENROUTER_API_KEY"), provider="openrouter"))
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

    return CompatLLM(
        vendor="groq",
        model="a-model",
        api_key="test-only-not-a-key",
        base_url=GROQ,
        http=http_client("groq", httpx.MockTransport(handle)),
        max_completion_tokens=50,
        reasoning_effort="none",
    )


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


def test_every_request_is_logged_with_its_settings_and_status_but_no_words(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="dafter.providers.openai_compat")
    ask(stubbed(200, sse("ठीक"), []), tools=[lookup_order])
    with pytest.raises(APIStatusError):
        ask(stubbed(429, json.dumps({"error": {"message": "slow down"}}).encode(), []))
    ok, limited = (r.__dict__ for r in caplog.records if r.getMessage() == "llm request")
    assert (ok["vendor"], ok["status"], ok["model"]) == ("groq", 200, "a-model")
    assert (ok["reasoning_effort"], ok["max_completion_tokens"]) == ("none", 50)
    assert (ok["messages"], ok["tools"], ok["parallel_tool_calls"]) == (1, "lookup_order", False)
    assert isinstance(ok["headers_ms"], int)
    assert limited["status"] == 429
    assert "नमस्ते" not in caplog.text and "slow down" not in caplog.text


def test_a_prewarm_opens_the_connection_the_chat_requests_reuse() -> None:
    seen: list[tuple[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url)))
        return httpx.Response(404)

    made = CompatLLM(
        vendor="groq",
        model="a-model",
        api_key="k",
        base_url=GROQ,
        http=http_client("groq", httpx.MockTransport(handle)),
    )

    async def run() -> None:
        made.prewarm()
        await asyncio.sleep(0.05)
        await made.aclose()

    asyncio.run(run())
    assert seen == [("HEAD", GROQ)]


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
    err = VENDORS["groq"].classify(failure(status, body), Stage.LLM)
    assert err.code is code
    assert err.provider is not None and err.provider.name == "groq"
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
    err = VENDORS["openrouter"].classify(exc, Stage.LLM)
    assert (err.code, err.retryable) == (code, retryable)


@pytest.mark.parametrize(
    "options",
    [{"baseUrl": "https://attacker.example/v1"}, {"endpoint": "https://attacker.example/v1"}],
)
def test_a_session_cannot_send_a_trusted_key_to_a_host_it_names(options: dict[str, Any]) -> None:
    with pytest.raises(DafterError) as caught:
        build(ref(**options))
    assert caught.value.code is ErrorCode.INVALID_CONFIG


def test_an_endpoint_reads_only_the_key_it_is_bound_to() -> None:
    with pytest.raises(DafterError) as caught:
        build(ref(provider="openrouter"))
    assert caught.value.code is ErrorCode.INVALID_CONFIG
    assert any("/agent/pipeline/llm/credentialRef" in d for d in caught.value.details)
