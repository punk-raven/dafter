from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from dafter_core.config import ResolvedSessionConfig
from dafter_core.enums import EventType
from dafter_core.hashing import seal
from dafter_runtime.plan import load
from dafter_scribe.quality import ScribeMetrics
from dafter_scribe.review import ReviewQueue
from dafter_scribe.sampling import Sampling
from dafter_scribe.scoring import ScoringLoop, session_facts
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions, llm
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from prometheus_client import CollectorRegistry

JOB = Path(__file__).resolve().parents[3] / "testdata" / "agent" / "hindi-webrtc-job.json"
ASHA = "p_4b81e0d7"
RAVI = "p_9c2e11aa"
PROMPT_TOKENS = 400
COMPLETION_TOKENS = 120


def job(**changes: Any) -> bytes:
    doc = json.loads(JOB.read_bytes())
    doc["transcription"] = {
        **doc["transcription"],
        "mode": "live",
        "consentArtifactId": "consent_tr",
    }
    doc["scribe"] = {**doc["scribe"], "enabled": True, "consentArtifactId": "consent_sc"}
    for key, value in changes.items():
        if isinstance(value, dict) and isinstance(doc.get(key), dict):
            merged = {**doc[key], **value}
            doc[key] = {k: v for k, v in merged.items() if v is not None}
        else:
            doc[key] = value
    sealed, _ = seal(json.dumps(doc))
    return sealed


def config(**changes: Any) -> ResolvedSessionConfig:
    return load(job(**changes))


def caption(sid: str, text: str, participant: str | None = None) -> dict[str, Any]:
    speaker = (
        {"kind": "agent"}
        if participant is None
        else {"kind": "human", "participantId": participant}
    )
    return {"segmentId": sid, "speaker": speaker, "text": text}


class StubStream(llm.LLMStream):
    def __init__(
        self,
        owner: ScriptedLLM,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(owner, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._step = owner.steps.pop(0) if owner.steps else owner.fallback
        self._tool = tools[0].id if tools else ""

    async def _run(self) -> None:
        step = self._step
        if isinstance(step, BaseException):
            raise step
        if isinstance(step, float):
            await asyncio.sleep(step)
            return
        call = llm.FunctionToolCall(name=self._tool, arguments=json.dumps(step), call_id="c_1")
        self._event_ch.send_nowait(
            llm.ChatChunk(id="stub", delta=llm.ChoiceDelta(role="assistant", tool_calls=[call]))
        )
        usage = llm.CompletionUsage(
            completion_tokens=COMPLETION_TOKENS,
            prompt_tokens=PROMPT_TOKENS,
            total_tokens=PROMPT_TOKENS + COMPLETION_TOKENS,
        )
        self._event_ch.send_nowait(llm.ChatChunk(id="stub", usage=usage))


Step = dict[str, Any] | BaseException | float


class ScriptedLLM(llm.LLM[Any]):
    def __init__(self, *steps: Step, fallback: Step | None = None) -> None:
        super().__init__()
        self.steps: list[Step] = list(steps)
        self.fallback: Step = fallback if fallback is not None else {"summary": "-"}
        self.requests: list[llm.ChatContext] = []
        self.choices: list[object] = []

    @property
    def model(self) -> str:
        return "sarvam-105b"

    @property
    def provider(self) -> str:
        return "Sarvam"

    def chat(
        self,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> llm.LLMStream:
        self.requests.append(chat_ctx.copy())
        self.choices.append(tool_choice)
        return StubStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


class Sent:
    def __init__(self) -> None:
        self.events: list[tuple[EventType, dict[str, Any]]] = []

    def __call__(self, event_type: EventType, payload: dict[str, Any]) -> bool:
        self.events.append((event_type, payload))
        return True

    def of(self, event_type: EventType) -> list[dict[str, Any]]:
        return [p for t, p in self.events if t is event_type]


def prompt_of(ctx: llm.ChatContext) -> tuple[str, str]:
    messages = [m for m in ctx.items if isinstance(m, llm.ChatMessage)]
    return messages[0].text_content or "", messages[1].text_content or ""


def scoring(
    rate: float = 1.0,
    cap: int = 200,
    queue: ReviewQueue | None = None,
    cfg: ResolvedSessionConfig | None = None,
    metrics: ScribeMetrics | None = None,
) -> ScoringLoop:
    resolved = cfg if cfg is not None else config()
    return ScoringLoop(
        sampling=Sampling(rate, cap),
        session=session_facts(resolved),
        metrics=metrics if metrics is not None else ScribeMetrics(CollectorRegistry()),
        place=("hi", "none", "stable"),
        queue=queue,
    )
