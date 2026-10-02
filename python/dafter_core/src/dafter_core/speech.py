from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .enums import Situation, SpeechNormalization

PhrasesByLanguage = dict[str, tuple[str, ...]]


def phrases_by_language(d: dict[str, Any] | None) -> PhrasesByLanguage:
    return {language: tuple(phrases) for language, phrases in (d or {}).items()}


def every_phrase(by_language: PhrasesByLanguage) -> tuple[str, ...]:
    return tuple(dict.fromkeys(p for phrases in by_language.values() for p in phrases))


@dataclass(frozen=True, slots=True)
class Backchannel:
    enabled: bool = True
    max_words: int = 3
    answer_within_ms: int = 1500
    words: PhrasesByLanguage = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Backchannel:
        return cls(
            enabled=d.get("enabled", True),
            max_words=d.get("maxWords", 3),
            answer_within_ms=d.get("answerWithinMs", 1500),
            words=phrases_by_language(d.get("words")),
        )


@dataclass(frozen=True, slots=True)
class Fillers:
    enabled: bool = True
    after_ms: int = 1000
    phrases: PhrasesByLanguage = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Fillers:
        return cls(
            enabled=d.get("enabled", True),
            after_ms=d.get("afterMs", 1000),
            phrases=phrases_by_language(d.get("phrases")),
        )


@dataclass(frozen=True, slots=True)
class Situations:
    enabled: bool = True
    cues: dict[Situation, PhrasesByLanguage] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Situations:
        return cls(
            enabled=d.get("enabled", True),
            cues={
                Situation(name): phrases_by_language(by_language)
                for name, by_language in (d.get("cues") or {}).items()
            },
        )


@dataclass(frozen=True, slots=True)
class Speech:
    normalization: SpeechNormalization = SpeechNormalization.PLATFORM
    substitutions: dict[str, dict[str, str]] = field(default_factory=dict)
    fillers: Fillers = field(default_factory=Fillers)
    situations: Situations = field(default_factory=Situations)
    expressive: bool = False

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Speech:
        return cls(
            normalization=SpeechNormalization(d.get("normalization", SpeechNormalization.PLATFORM)),
            substitutions={k: dict(v) for k, v in (d.get("substitutions") or {}).items()},
            fillers=Fillers.from_dict(d.get("fillers") or {}),
            situations=Situations.from_dict(d.get("situations") or {}),
            expressive=d.get("expressive", False),
        )


__all__ = [
    "Backchannel",
    "Fillers",
    "PhrasesByLanguage",
    "Situations",
    "Speech",
    "every_phrase",
    "phrases_by_language",
]
