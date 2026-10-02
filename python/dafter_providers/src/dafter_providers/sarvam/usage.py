from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from types import TracebackType
from typing import Any

import openai
from openai.types.chat import ChatCompletionChunk
from openai.types.completion_usage import CompletionTokensDetails


def with_reasoning(chunk: ChatCompletionChunk) -> ChatCompletionChunk:
    usage = chunk.usage
    if usage is None or usage.completion_tokens_details is not None:
        return chunk
    reasoning = (usage.model_extra or {}).get("reasoning_tokens")
    if isinstance(reasoning, int):
        usage.completion_tokens_details = CompletionTokensDetails(reasoning_tokens=reasoning)
    return chunk


class ReasoningStream:
    def __init__(self, stream: openai.AsyncStream[ChatCompletionChunk]) -> None:
        self._stream = stream

    async def __aenter__(self) -> ReasoningStream:
        await self._stream.__aenter__()
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        exc: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        await self._stream.__aexit__(kind, exc, trace)

    async def __aiter__(self) -> AsyncIterator[ChatCompletionChunk]:
        async for chunk in self._stream:
            yield with_reasoning(chunk)

    async def close(self) -> None:
        await self._stream.close()


class ReportedCreate:
    def __init__(self, create: Callable[..., Awaitable[Any]]) -> None:
        self._create = create

    async def __call__(self, **kwargs: Any) -> Any:
        made = await self._create(**kwargs)
        if isinstance(made, openai.AsyncStream):
            return ReasoningStream(made)
        return made


def report_reasoning(client: openai.AsyncClient) -> None:
    completions = client.chat.completions
    if not isinstance(completions.create, ReportedCreate):
        completions.create = ReportedCreate(completions.create)  # type: ignore[method-assign]
