from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    rule: str
    message: str

    def render(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}: {self.message}"
