from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dafter_core.config import ProviderRef, ResolvedSessionConfig
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError
from dafter_evals.screen import bank
from dafter_providers import Vendor, vendor_for
from dafter_runtime.personas import base_language
from dafter_runtime.plan import check_encryption
from livekit.agents import llm

LLM_AT = "/scribe/llm"
JUDGE_AT = "/scribe/judge"
AGENT_LABEL = "Agent"


@dataclass(frozen=True, slots=True)
class Language:
    tag: str
    name: str
    script: str | None

    def instruction(self) -> str:
        written = f" in {self.script} script" if self.script else ""
        return (
            f"Write every field in {self.name}{written}, the language of the call, even where "
            "people mix in English words."
        )


@dataclass(frozen=True, slots=True)
class Model:
    vendor: Vendor
    ref: ProviderRef
    at: str

    def build(self) -> llm.LLM[Any]:
        if self.vendor.llm is None:
            raise DafterError(ErrorCode.INTERNAL, "a planned vendor lost its llm factory")
        return self.vendor.llm(self.ref, self.at)

    def source(self) -> dict[str, str]:
        return {"provider": self.ref.provider, "model": self.ref.model or "unknown"}


@dataclass(frozen=True, slots=True)
class ScribePlan:
    config: ResolvedSessionConfig
    writer: Model
    judge: Model | None
    language: Language
    agent_label: str
    interval_s: float
    after_call_s: float


def _refuse(code: ErrorCode, message: str, pointer: str, because: str) -> DafterError:
    return DafterError(code, message, details=(f"at '{pointer}': {because}",))


def language_of(tag: str) -> Language:
    base = base_language(tag)
    if base not in bank.LANGUAGES:
        return Language(tag, tag, None)
    known = bank.load(base)
    return Language(tag, known.name, known.script)


def _model(ref: ProviderRef | None, at: str, language: str) -> Model:
    if ref is None:
        raise _refuse(ErrorCode.INVALID_CONFIG, "the scribe names no llm", at, "required")
    vendor = vendor_for(ref, Stage.LLM, at)
    if language not in vendor.languages:
        raise _refuse(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "the scribe's llm does not serve this session's language",
            "/language",
            f"not declared by {vendor.name}",
        )
    return Model(vendor, ref, at)


def plan(cfg: ResolvedSessionConfig, pool: str, fetches_keys: bool = False) -> ScribePlan:
    scribe = cfg.scribe
    if not scribe.enabled:
        raise _refuse(
            ErrorCode.INVALID_CONFIG,
            "a scribe job arrived for a session without a scribe",
            "/scribe/enabled",
            "false",
        )
    if scribe.pool != pool:
        raise _refuse(
            ErrorCode.INVALID_CONFIG,
            "the job names another worker pool",
            "/scribe/pool",
            f"this worker serves {pool}",
        )
    check_encryption(cfg, fetches_keys)
    return ScribePlan(
        config=cfg,
        writer=_model(scribe.llm, LLM_AT, cfg.language),
        judge=_model(scribe.judge, JUDGE_AT, cfg.language) if scribe.judge else None,
        language=language_of(cfg.language),
        agent_label=cfg.agent.name or AGENT_LABEL,
        interval_s=scribe.summary_interval_ms / 1000,
        after_call_s=float(scribe.after_call_timeout_seconds),
    )


__all__ = [
    "AGENT_LABEL",
    "JUDGE_AT",
    "LLM_AT",
    "Language",
    "Model",
    "ScribePlan",
    "language_of",
    "plan",
]
