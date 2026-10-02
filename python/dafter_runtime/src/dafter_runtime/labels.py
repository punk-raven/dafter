from __future__ import annotations

import re
from collections.abc import AsyncIterable, AsyncIterator
from typing import Any

from livekit.agents import llm as lk_llm

LABEL = re.compile(r"^\s*\[[^\]\n]{1,80}\]\s*")
LONGEST_LABEL = 84


def _text(piece: Any) -> str | None:
    if isinstance(piece, str):
        return piece
    if isinstance(piece, lk_llm.ChatChunk) and piece.delta is not None:
        return None if piece.delta.tool_calls else piece.delta.content or ""
    return None


def _textless(piece: Any) -> bool:
    return isinstance(piece, lk_llm.ChatChunk) and piece.delta is None


def _with_text(piece: Any, text: str) -> Any:
    if isinstance(piece, str):
        return text
    delta = piece.delta.model_copy(update={"content": text})
    return piece.model_copy(update={"delta": delta})


def _undecided(line: str) -> bool:
    said = line.lstrip()
    return said == "" or (
        said.startswith("[") and "]" not in said and "\n" not in said and len(said) < LONGEST_LABEL
    )


class Lines:
    def __init__(self) -> None:
        self._held = ""
        self._line_start = True

    def feed(self, text: str) -> str:
        self._held += text
        spoken = ""
        while self._held:
            if self._line_start:
                if _undecided(self._held):
                    break
                label = LABEL.match(self._held)
                if label is not None:
                    self._held = self._held[label.end() :]
                self._line_start = False
                continue
            end = self._held.find("\n")
            if end < 0:
                spoken, self._held = spoken + self._held, ""
                break
            spoken, self._held = spoken + self._held[: end + 1], self._held[end + 1 :]
            self._line_start = True
        return spoken

    def rest(self) -> str:
        held, self._held = self._held, ""
        return held


async def unlabeled(reply: AsyncIterable[Any]) -> AsyncIterator[Any]:
    lines = Lines()
    last: Any = None
    async for piece in reply:
        said = _text(piece)
        if said is None:
            if not _textless(piece) and last is not None and (rest := lines.rest()):
                yield _with_text(last, rest)
            yield piece
            continue
        last = piece
        if spoken := lines.feed(said):
            yield _with_text(piece, spoken)
    if last is not None and (rest := lines.rest()):
        yield _with_text(last, rest)
