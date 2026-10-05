from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol

Rendering = Literal["verbatim", "clean"]
RENDERINGS: tuple[Rendering, ...] = ("verbatim", "clean")


@dataclass(frozen=True, slots=True)
class AudioFile:
    name: str
    data: bytes
    content_type: str


@dataclass(frozen=True, slots=True)
class Chunk:
    text: str
    start_s: float
    end_s: float


@dataclass(frozen=True, slots=True)
class FileTranscript:
    name: str
    text: str
    language: str | None
    chunks: tuple[Chunk, ...]


class BatchTranscriber(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    async def transcribe(
        self, files: Sequence[AudioFile], language: str, rendering: Rendering
    ) -> Mapping[str, FileTranscript]: ...


__all__ = ["RENDERINGS", "AudioFile", "BatchTranscriber", "Chunk", "FileTranscript", "Rendering"]
