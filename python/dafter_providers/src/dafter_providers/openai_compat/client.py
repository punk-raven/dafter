from __future__ import annotations

from typing import Any, Literal

import httpx
import openai
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions, llm
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.plugins import openai as plugin

Effort = Literal["none", "minimal", "low", "medium", "high"]


class CompatLLM(plugin.LLM):
    def __init__(
        self, *, vendor: str, api_key: str, base_url: str, http: httpx.AsyncClient, **kwargs: Any
    ) -> None:
        client = openai.AsyncClient(
            api_key=api_key, base_url=base_url, max_retries=0, http_client=http
        )
        super().__init__(client=client, **kwargs)
        self._vendor = vendor
        self._http = http
        self._base_url = base_url

    async def _prewarm_impl(self) -> None:
        await self._http.head(self._base_url)

    async def aclose(self) -> None:
        await super().aclose()
        await self._client.close()

    @property
    def provider(self) -> str:
        return self._vendor

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        response_format: NotGivenOr[Any] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> plugin.llm.LLMStream:
        return super().chat(
            chat_ctx=chat_ctx,
            tools=tools,
            conn_options=conn_options,
            parallel_tool_calls=False if tools else NOT_GIVEN,
            tool_choice=tool_choice,
            response_format=response_format,
            extra_kwargs=extra_kwargs,
        )
