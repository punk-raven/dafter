from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from dafter_runtime.labels import unlabeled
from livekit.agents import llm


def chunk(content: str | None, **delta: Any) -> llm.ChatChunk:
    return llm.ChatChunk(id="c", delta=llm.ChoiceDelta(role="assistant", content=content, **delta))


def spoken(pieces: list[Any]) -> list[Any]:
    async def reply() -> AsyncIterator[Any]:
        for piece in pieces:
            yield piece

    async def run() -> list[Any]:
        return [p async for p in unlabeled(reply())]

    return asyncio.run(run())


def text(pieces: list[Any]) -> str:
    return "".join(p if isinstance(p, str) else (p.delta.content or "") for p in pieces)


@pytest.mark.parametrize(
    "parts",
    [
        ["[Speaker 1, to you] चाय में 5 से 10 minutes लगते हैं।"],
        ["[Spea", "ker 1", "] ", "चाय में 5 से 10 minutes लगते हैं।"],
        ["", "[Asha, to you]", " चाय में 5 से 10 minutes लगते हैं।"],
    ],
)
def test_a_speaker_label_the_model_copied_is_never_spoken(parts: list[str]) -> None:
    assert text(spoken([chunk(p) for p in parts])).strip() == "चाय में 5 से 10 minutes लगते हैं।"


def test_a_reply_without_a_label_passes_through_unchanged() -> None:
    pieces = [chunk("चाय में "), chunk("5 मिनट लगते हैं।")]
    assert spoken(pieces) == pieces
    assert spoken(["जी, ", "बताइए।"]) == ["जी, ", "बताइए।"]


def test_a_bracket_that_is_not_a_label_is_kept() -> None:
    long = "[" + "x" * 100
    assert text(spoken([chunk(long), chunk(" and more")])) == long + " and more"
    assert text(spoken([chunk("[unclosed")])) == "[unclosed"


def test_a_tool_call_ends_the_wait_and_passes_untouched() -> None:
    call = llm.FunctionToolCall(name="current_time", arguments="{}", call_id="t1")
    pieces = [chunk("[Spea"), chunk(None, tool_calls=[call])]
    assert spoken(pieces) == pieces


def test_a_label_on_any_line_is_dropped_and_the_words_kept() -> None:
    parts = ["[Speaker 1, to you] निव्या, मुझे बताओ", "\n[Speaker 1, to you] ठीक है।\n\nपाँच मिनट।"]
    assert text(spoken([chunk(p) for p in parts])) == "निव्या, मुझे बताओ\nठीक है।\n\nपाँच मिनट।"


def test_chunks_without_text_do_not_end_the_wait_for_a_label() -> None:
    empty = llm.ChatChunk(id="c", delta=None)
    pieces = [empty, empty, chunk("[Speaker 1] "), chunk("जी, बताइए।")]
    out = spoken(pieces)
    assert out[:2] == [empty, empty]
    assert text(out[2:]) == "जी, बताइए।"
