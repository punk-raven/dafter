from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LanguageSwitching:
    enabled: bool = False
    languages: tuple[str, ...] = ()
    min_confidence: float = 0.8
    min_words: int = 3

    def leaves_out(self, language: str) -> bool:
        return self.enabled and language not in self.languages

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> LanguageSwitching:
        return cls(
            enabled=d.get("enabled", False),
            languages=tuple(d.get("languages") or ()),
            min_confidence=float(d.get("minConfidence", 0.8)),
            min_words=d.get("minWords", 3),
        )


__all__ = ["LanguageSwitching"]
