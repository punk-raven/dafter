from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from dafter_core.enums import Stage
from dafter_core.errors import DafterError
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectionError, APIConnectOptions
from livekit.agents import llm as lk_llm
from livekit.agents import tts as lk_tts
from livekit.agents.types import NOT_GIVEN, NotGivenOr

from .multilingual import Multilingual
from .styled import Styled

FAULT_ENV = "DAFTER_INJECT_FAULT"
FAILOVER_STAGES = (Stage.LLM, Stage.TTS)
INJECTED_FAULT = "injected fault: the primary provider is forced to fail"
RETRIES_BEFORE_SWITCH = 0
LLM_ATTEMPT_TIMEOUT_S = 5.0
SWITCH_EVENTS = {Stage.LLM: "llm_availability_changed", Stage.TTS: "tts_availability_changed"}

Classify = Callable[[BaseException, Stage], DafterError]
StageError = lk_llm.LLMError | lk_tts.TTSError


def injected_faults(env: Mapping[str, str] = os.environ) -> frozenset[Stage]:
    named = {part.strip() for part in env.get(FAULT_ENV, "").split(",") if part.strip()}
    known = {str(stage) for stage in FAILOVER_STAGES}
    unknown = sorted(named - known)
    if unknown:
        raise ValueError(
            f"{FAULT_ENV} names stages without failover: {', '.join(unknown)}; "
            f"known: {', '.join(sorted(known))}"
        )
    return frozenset(Stage(name) for name in named)


class ClassifiedFailureError(Exception):
    def __init__(self, error: DafterError) -> None:
        super().__init__(error.message)
        self.error = error


def classified_first(classify: Classify) -> Classify:
    def classify_chain(exc: BaseException, stage: Stage) -> DafterError:
        if isinstance(exc, ClassifiedFailureError):
            return exc.error
        return classify(exc, stage)

    return classify_chain


class FaultyLLM(lk_llm.LLM[Any]):
    def __init__(self, primary: lk_llm.LLM[Any]) -> None:
        super().__init__()
        self._primary = primary
        self._label = primary.label

    @property
    def model(self) -> str:
        return self._primary.model

    @property
    def provider(self) -> str:
        return self._primary.provider

    def chat(
        self,
        *,
        chat_ctx: lk_llm.ChatContext,
        tools: list[lk_llm.Tool] | None = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
        parallel_tool_calls: NotGivenOr[bool] = NOT_GIVEN,
        tool_choice: NotGivenOr[lk_llm.ToolChoice] = NOT_GIVEN,
        extra_kwargs: NotGivenOr[dict[str, Any]] = NOT_GIVEN,
    ) -> lk_llm.LLMStream:
        return _FaultyCompletion(
            self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options
        )


class _FaultyCompletion(lk_llm.LLMStream):
    async def _run(self) -> None:
        raise APIConnectionError(INJECTED_FAULT, retryable=False)


class FaultyTTS(lk_tts.TTS[Any]):
    def __init__(self, primary: lk_tts.TTS[Any]) -> None:
        super().__init__(
            capabilities=primary.capabilities,
            sample_rate=primary.sample_rate,
            num_channels=primary.num_channels,
        )
        self._primary = primary
        self._label = primary.label

    @property
    def model(self) -> str:
        return self._primary.model

    @property
    def provider(self) -> str:
        return self._primary.provider

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> lk_tts.ChunkedStream:
        return _FaultyChunks(tts=self, input_text=text, conn_options=conn_options)

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> lk_tts.SynthesizeStream:
        return _FaultySynthesis(tts=self, conn_options=conn_options)


class _FaultyChunks(lk_tts.ChunkedStream):
    async def _run(self, output_emitter: lk_tts.AudioEmitter) -> None:
        raise APIConnectionError(INJECTED_FAULT, retryable=False)


class _FaultySynthesis(lk_tts.SynthesizeStream):
    async def _run(self, output_emitter: lk_tts.AudioEmitter) -> None:
        raise APIConnectionError(INJECTED_FAULT, retryable=False)


class FailoverTTS(lk_tts.FallbackAdapter):
    def speak_in(self, language: str) -> None:
        for instance in self._tts_instances:
            if isinstance(instance, Multilingual):
                instance.speak_in(language)

    def style(self, situation: str) -> None:
        for instance in self._tts_instances:
            if isinstance(instance, Styled):
                instance.style(situation)


def forward_errors(
    adapter: Any, stage: Stage, instances: Sequence[Any], classifiers: Sequence[Classify]
) -> None:
    def forwarder(classify: Classify) -> Callable[[StageError], None]:
        def forward(error: StageError) -> None:
            failure = ClassifiedFailureError(classify(error.error, stage))
            adapter.emit("error", error.model_copy(update={"recoverable": True, "error": failure}))

        return forward

    for instance, classify in zip(instances, classifiers, strict=True):
        instance.on("error", forwarder(classify))


def follow_switches(adapter: Any, stage: Stage, switched: Callable[[], None]) -> None:
    def changed(event: Any) -> None:
        if not event.available:
            switched()

    adapter.on(SWITCH_EVENTS[stage], changed)


def llm(
    primary: lk_llm.LLM[Any],
    fallbacks: Sequence[lk_llm.LLM[Any]],
    faulted: bool,
    classifiers: Sequence[Classify],
) -> lk_llm.LLM[Any]:
    if not fallbacks and not faulted:
        return primary
    instances = [FaultyLLM(primary) if faulted else primary, *fallbacks]
    adapter = lk_llm.FallbackAdapter(
        instances,
        attempt_timeout=LLM_ATTEMPT_TIMEOUT_S,
        max_retry_per_llm=RETRIES_BEFORE_SWITCH,
        retry_on_chunk_sent=False,
    )
    forward_errors(adapter, Stage.LLM, instances, classifiers)
    return adapter


def tts(
    primary: lk_tts.TTS[Any],
    fallbacks: Sequence[lk_tts.TTS[Any]],
    faulted: bool,
    classifiers: Sequence[Classify],
) -> lk_tts.TTS[Any]:
    if not fallbacks and not faulted:
        return primary
    instances = [FaultyTTS(primary) if faulted else primary, *fallbacks]
    adapter = FailoverTTS(instances, max_retry_per_tts=RETRIES_BEFORE_SWITCH)
    forward_errors(adapter, Stage.TTS, instances, classifiers)
    return adapter
