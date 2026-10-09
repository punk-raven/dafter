from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

NO_NOISE_FILTER = "off"


@dataclass(frozen=True, slots=True)
class ProviderRef:
    provider: str
    model: str | None = None
    region: str | None = None
    credential_ref: str | None = None
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProviderRef:
        return cls(
            provider=d["provider"],
            model=d.get("model"),
            region=d.get("region"),
            credential_ref=d.get("credentialRef"),
            options=d.get("options") or {},
        )


@dataclass(frozen=True, slots=True)
class Fallback:
    llm: tuple[ProviderRef, ...] = ()
    tts: tuple[ProviderRef, ...] = ()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Fallback:
        return cls(
            llm=tuple(ProviderRef.from_dict(r) for r in d.get("llm") or ()),
            tts=tuple(ProviderRef.from_dict(r) for r in d.get("tts") or ()),
        )


@dataclass(frozen=True, slots=True)
class Pipeline:
    vad: ProviderRef | None = None
    stt: ProviderRef | None = None
    llm: ProviderRef | None = None
    tts: ProviderRef | None = None
    mt: ProviderRef | None = None
    realtime: ProviderRef | None = None
    fallback: Fallback = field(default_factory=Fallback)
    noise_filter: str = NO_NOISE_FILTER

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Pipeline:
        def ref(key: str) -> ProviderRef | None:
            v = d.get(key)
            return ProviderRef.from_dict(v) if v else None

        return cls(
            vad=ref("vad"),
            stt=ref("stt"),
            llm=ref("llm"),
            tts=ref("tts"),
            mt=ref("mt"),
            realtime=ref("realtime"),
            fallback=Fallback.from_dict(d.get("fallback") or {}),
            noise_filter=d.get("noiseFilter") or NO_NOISE_FILTER,
        )


__all__ = ["NO_NOISE_FILTER", "Fallback", "Pipeline", "ProviderRef"]
