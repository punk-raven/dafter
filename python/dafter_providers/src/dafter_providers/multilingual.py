from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Multilingual(Protocol):
    def speak_in(self, language: str) -> None: ...


__all__ = ["Multilingual"]
