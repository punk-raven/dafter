from __future__ import annotations

from typing import Any

from dafter_core.config import ProviderRef
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError, ProviderContext
from livekit.agents import APIConnectionError, APIStatusError, APITimeoutError, llm
from livekit.agents.types import NOT_GIVEN, NotGivenOr

from .. import credentials
from ..options import Options
from .client import NAME, CompatLLM, Effort
from .endpoints import ENDPOINTS, Endpoint

LANGUAGES = frozenset({"hi", "hi-IN"})
EFFORTS: dict[str, Effort] = {
    "none": "none",
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
}
QUOTA_CODES = frozenset(
    {
        "insufficient_quota",
        "credit_balance_exhausted",
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
    }
)
TIMEOUT_STATUSES = frozenset({408, 504})
CONFIG_STATUSES = frozenset({400, 404, 422})
OPTIONS = ("endpoint", "temperature", "maxTokens", "reasoningEffort", "extraBody")
AGENT_LLM = "/agent/pipeline/llm"

__all__ = [
    "ENDPOINTS",
    "LANGUAGES",
    "NAME",
    "Endpoint",
    "build_llm",
    "classify",
    "wants_prewarm",
]


def _refuse(code: ErrorCode, message: str, pointer: str, because: str) -> DafterError:
    return DafterError(
        code,
        message,
        stage=Stage.LLM,
        provider=ProviderContext(NAME),
        details=(f"at '{pointer}': {because}",),
    )


def _model(ref: ProviderRef, at: str) -> str:
    if not ref.model:
        raise _refuse(
            ErrorCode.INVALID_CONFIG,
            f"{NAME} llm names no model",
            f"{at}/model",
            "the endpoint's model id, pinned",
        )
    if ref.region is not None:
        raise _refuse(
            ErrorCode.RESIDENCY_VIOLATION,
            f"{NAME} cannot vouch for where an endpoint serves, so it takes no region pin",
            f"{at}/region",
            "must be absent",
        )
    return ref.model


def _effort(opts: Options, raw: dict[str, Any]) -> NotGivenOr[Effort]:
    if "reasoningEffort" not in raw:
        return NOT_GIVEN
    return opts.choice("reasoningEffort", EFFORTS, None)


def build_llm(ref: ProviderRef, at: str = AGENT_LLM) -> llm.LLM[Any]:
    model = _model(ref, at)
    opts = Options(Stage.LLM, NAME, ref.options, OPTIONS, base=at)
    endpoint = ENDPOINTS.get(opts.get("endpoint", str, ""))
    if endpoint is None:
        raise opts.error(
            "an OpenAI-compatible stage names an endpoint this worker binds",
            f"at '{opts.pointer('endpoint')}': one of {', '.join(ENDPOINTS)}",
        )
    temperature = opts.get("temperature", float, 0.4)
    max_tokens = opts.get("maxTokens", int, 200)
    effort = _effort(opts, ref.options)
    extra_body: dict[str, Any] = opts.get("extraBody", dict, {})
    key = credentials.resolve(ref, Stage.LLM, {endpoint.credential_env}, pointer=at)
    try:
        return CompatLLM(
            model=model,
            api_key=key,
            base_url=endpoint.base_url,
            temperature=temperature,
            max_completion_tokens=max_tokens,
            reasoning_effort=effort,
            extra_body=extra_body or NOT_GIVEN,
        )
    except ValueError as exc:
        raise opts.error(f"{NAME} refused the llm settings at construction: {exc}") from exc


def wants_prewarm(ref: ProviderRef) -> bool:
    return False


def _billing(body: object) -> bool:
    if isinstance(body, list):
        return any(_billing(item) for item in body)
    if isinstance(body, dict):
        if body.get("code") in QUOTA_CODES or body.get("type") in QUOTA_CODES:
            return True
        return _billing(body.get("error"))
    return False


def _status_code(exc: APIStatusError) -> ErrorCode:
    status = exc.status_code
    if status in (401, 403):
        return ErrorCode.AUTHENTICATION_FAILED
    if status == 402:
        return ErrorCode.QUOTA_EXCEEDED
    if status == 429:
        return ErrorCode.QUOTA_EXCEEDED if _billing(exc.body) else ErrorCode.RATE_LIMITED
    if status in CONFIG_STATUSES:
        return ErrorCode.INVALID_CONFIG
    if status in TIMEOUT_STATUSES:
        return ErrorCode.PROVIDER_TIMEOUT
    return ErrorCode.PROVIDER_UNAVAILABLE


def classify(exc: BaseException, stage: Stage) -> DafterError:
    context = ProviderContext(NAME)
    code = ErrorCode.INTERNAL
    if isinstance(exc, APITimeoutError):
        code = ErrorCode.PROVIDER_TIMEOUT
    elif isinstance(exc, APIStatusError):
        code = _status_code(exc)
        context = ProviderContext(NAME, request_id=exc.request_id, native_code=str(exc.status_code))
    elif isinstance(exc, APIConnectionError):
        code = ErrorCode.PROVIDER_UNAVAILABLE
    return DafterError(
        code, f"{NAME} {stage} failed ({type(exc).__name__})", stage=stage, provider=context
    )
