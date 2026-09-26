from __future__ import annotations

from typing import Any

from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions, llm
from livekit.agents.types import NOT_GIVEN, NotGivenOr

REPLY = "नमस्ते! बताइए, मैं क्या मदद करूँ?"
PROMPT_TOKENS = 50
COMPLETION_TOKENS = 7


class StubStream(llm.LLMStream):
    def __init__(
        self,
        owner: StubLLM,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(owner, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._reply = owner.reply
        self._call = owner.calls.pop(0) if owner.calls else None

    async def _run(self) -> None:
        delta = llm.ChoiceDelta(role="assistant", content=self._reply)
        if self._call is not None:
            call = llm.FunctionToolCall(name=self._call, arguments="{}", call_id=f"c_{self._call}")
            delta = llm.ChoiceDelta(role="assistant", tool_calls=[call])
        usage = llm.CompletionUsage(
            completion_tokens=COMPLETION_TOKENS,
            prompt_tokens=PROMPT_TOKENS,
            total_tokens=PROMPT_TOKENS + COMPLETION_TOKENS,
        )
        self._event_ch.send_nowait(llm.ChatChunk(id="stub", delta=delta))
        self._event_ch.send_nowait(llm.ChatChunk(id="stub", usage=usage))


class StubLLM(llm.LLM[Any]):
    def __init__(self, reply: str = REPLY, calls: list[str] | None = None) -> None:
        super().__init__()
        self.reply = reply
        self.calls = list(calls or [])
        self.requests: list[llm.ChatContext] = []
        self.offered: list[list[str]] = []

    @property
    def model(self) -> str:
        return "sarvam-105b"

    @property
    def provider(self) -> str:
        return "Sarvam"

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> llm.LLMStream:
        self.requests.append(chat_ctx.copy())
        self.offered.append(sorted(t.id for t in tools or []))
        return StubStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


def said(ctx: llm.ChatContext) -> list[tuple[str, str | None]]:
    return [(m.role, m.text_content) for m in ctx.items if isinstance(m, llm.ChatMessage)]
