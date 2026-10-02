from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Styled(Protocol):
    def style(self, situation: str) -> None: ...


__all__ = ["Styled"]
