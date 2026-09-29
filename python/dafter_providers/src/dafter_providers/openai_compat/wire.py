from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx

log = logging.getLogger("dafter.providers.openai_compat")

TIMEOUT = httpx.Timeout(connect=15.0, read=5.0, write=5.0, pool=5.0)
LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=50, keepalive_expiry=120)
SENT_AT = "dafter.sent_at"
RESPONSE_HEADERS = {
    "x-request-id": "request_id",
    "x-ratelimit-remaining-requests": "remaining_requests",
    "x-ratelimit-remaining-tokens": "remaining_tokens",
    "retry-after": "retry_after",
}


def described(body: bytes) -> dict[str, Any]:
    try:
        sent = json.loads(body)
    except ValueError:
        return {}
    if not isinstance(sent, dict):
        return {}
    messages = sent.get("messages") or []
    shown = {
        k: v
        for k, v in sent.items()
        if k not in ("messages", "tools", "stream_options") and not isinstance(v, (dict, list))
    }
    return {
        **shown,
        "messages": len(messages),
        "tools": ",".join(t.get("function", {}).get("name", "") for t in sent.get("tools") or []),
        "extra": ",".join(
            k for k, v in sent.items() if isinstance(v, dict) and k != "stream_options"
        ),
    }


def http_client(
    vendor: str, transport: httpx.AsyncBaseTransport | None = None
) -> httpx.AsyncClient:
    async def sent(request: httpx.Request) -> None:
        request.extensions[SENT_AT] = time.monotonic()

    async def answered(response: httpx.Response) -> None:
        request = response.request
        started = request.extensions.get(SENT_AT)
        fields: dict[str, Any] = {
            "vendor": vendor,
            "path": request.url.path,
            "status": response.status_code,
            "http": response.http_version,
            "headers_ms": round((time.monotonic() - started) * 1000) if started else None,
            **{
                name: response.headers[h]
                for h, name in RESPONSE_HEADERS.items()
                if h in response.headers
            },
        }
        if request.method != "POST":
            log.info("llm connection warmed", extra=fields)
            return
        log.info("llm request", extra={**fields, **described(request.content)})

    return httpx.AsyncClient(
        http2=True,
        transport=transport,
        timeout=TIMEOUT,
        limits=LIMITS,
        follow_redirects=True,
        event_hooks={"request": [sent], "response": [answered]},
    )
