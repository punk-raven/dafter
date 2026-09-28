from __future__ import annotations

from typing import Any

from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions, llm
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.plugins import openai, sarvam

from .usage import report_reasoning


class SarvamLLM(sarvam.LLM):
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
    ) -> openai.llm.LLMStream:
        report_reasoning(self._client)
        stream: openai.llm.LLMStream = super().chat(
            chat_ctx=chat_ctx,
            tools=tools,
            conn_options=conn_options,
            parallel_tool_calls=False if tools else NOT_GIVEN,
            tool_choice=tool_choice,
            response_format=response_format,
            extra_kwargs=extra_kwargs,
        )
        return stream
