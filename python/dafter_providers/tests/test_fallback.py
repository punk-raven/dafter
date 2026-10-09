from __future__ import annotations

import asyncio
from typing import Any

import pytest
from dafter_core.enums import ErrorCode, Stage
from dafter_core.errors import DafterError, ProviderContext
from dafter_providers import fallback
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectionError, APIConnectOptions
from livekit.agents import llm as lk_llm
from livekit.agents import tts as lk_tts
from livekit.agents.types import NOT_GIVEN, NotGivenOr

REPLY = "fallback reply"


def named(name: str) -> fallback.Classify:
    def classify(exc: BaseException, stage: Stage) -> DafterError:
        return DafterError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            f"{name} {stage} failed",
            stage=stage,
            provider=ProviderContext(name),
        )

    return classify


PAIR = [named("sarvam"), named("groq")]


class AnsweringStream(lk_llm.LLMStream):
    async def _run(self) -> None:
        delta = lk_llm.ChoiceDelta(role="assistant", content=REPLY)
        self._event_ch.send_nowait(lk_llm.ChatChunk(id="answer", delta=delta))


class AnsweringLLM(lk_llm.LLM[Any]):
    def __init__(self, provider: str = "groq", model: str = "qwen") -> None:
        super().__init__()
        self._provider = provider
        self._model = model

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return self._provider

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
        return AnsweringStream(
            self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options
        )


class SilentTTS(lk_tts.TTS[Any]):
    def __init__(self) -> None:
        super().__init__(
            capabilities=lk_tts.TTSCapabilities(streaming=False),
            sample_rate=24000,
            num_channels=1,
        )
        self.languages: list[str] = []
        self.situations: list[str] = []

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> lk_tts.ChunkedStream:
        raise NotImplementedError

    def speak_in(self, language: str) -> None:
        self.languages.append(language)

    def style(self, situation: str) -> None:
        self.situations.append(situation)


def asked(model: lk_llm.LLM[Any]) -> str:
    async def run() -> str:
        chat_ctx = lk_llm.ChatContext()
        chat_ctx.add_message(role="user", content="hello")
        stream = model.chat(chat_ctx=chat_ctx)
        try:
            texts = [chunk.delta.content async for chunk in stream if chunk.delta]
            return "".join(text for text in texts if text)
        finally:
            await stream.aclose()

    return asyncio.run(run())


def test_no_fault_is_injected_by_default() -> None:
    assert fallback.injected_faults({}) == frozenset()


def test_the_fault_flag_names_stages_by_their_schema_names() -> None:
    faults = fallback.injected_faults({fallback.FAULT_ENV: "llm, tts"})
    assert faults == frozenset({Stage.LLM, Stage.TTS})


def test_the_fault_flag_refuses_a_stage_without_failover() -> None:
    with pytest.raises(ValueError, match="vad"):
        fallback.injected_faults({fallback.FAULT_ENV: "llm,vad"})


def test_speech_to_text_never_fails_over() -> None:
    with pytest.raises(ValueError, match="stt"):
        fallback.injected_faults({fallback.FAULT_ENV: "stt"})


def test_a_stage_without_fallbacks_or_fault_keeps_its_own_provider() -> None:
    primary = AnsweringLLM()
    assert fallback.llm(primary, [], False, [named("sarvam")]) is primary


def test_an_injected_fault_fails_the_primary_and_the_fallback_answers() -> None:
    primary = AnsweringLLM("sarvam", "sarvam-m")
    adapter = fallback.llm(primary, [AnsweringLLM()], True, PAIR)
    assert isinstance(adapter, lk_llm.FallbackAdapter)
    assert asked(adapter) == REPLY


def test_the_faulty_primary_keeps_the_primary_name_for_metrics() -> None:
    faulty = fallback.FaultyLLM(AnsweringLLM("sarvam", "sarvam-m"))
    assert (faulty.provider, faulty.model) == ("sarvam", "sarvam-m")


def test_a_fault_without_a_fallback_fails_the_stage() -> None:
    adapter = fallback.llm(AnsweringLLM(), [], True, [named("sarvam")])
    with pytest.raises(APIConnectionError):
        asked(adapter)


def test_an_instance_failure_reaches_the_session_as_a_recoverable_error() -> None:
    primary, second = AnsweringLLM("sarvam", "sarvam-m"), AnsweringLLM()
    adapter = fallback.llm(primary, [second], False, PAIR)
    seen: list[lk_llm.LLMError] = []
    adapter.on("error", seen.append)
    failure = APIConnectionError("down")
    second.emit(
        "error", lk_llm.LLMError(timestamp=0.0, label="groq", error=failure, recoverable=False)
    )
    [forwarded] = seen
    assert forwarded.recoverable
    assert isinstance(forwarded.error, fallback.ClassifiedFailureError)
    err = forwarded.error.error
    assert err.provider is not None and err.provider.name == "groq"


def test_a_classified_failure_keeps_the_vendor_that_failed() -> None:
    classify = fallback.classified_first(named("sarvam"))
    failure = fallback.ClassifiedFailureError(named("groq")(APIConnectionError(), Stage.LLM))
    err = classify(failure, Stage.LLM)
    assert err.provider is not None and err.provider.name == "groq"
    raw = classify(APIConnectionError(), Stage.LLM)
    assert raw.provider is not None and raw.provider.name == "sarvam"


def test_a_provider_marked_unavailable_counts_as_a_switch() -> None:
    adapter = fallback.llm(AnsweringLLM("sarvam", "sarvam-m"), [AnsweringLLM()], False, PAIR)
    switches: list[None] = []
    fallback.follow_switches(adapter, Stage.LLM, lambda: switches.append(None))
    assert isinstance(adapter, lk_llm.FallbackAdapter)
    primary = adapter._llm_instances[0]
    adapter.emit("llm_availability_changed", lk_llm.AvailabilityChangedEvent(primary, False))
    adapter.emit("llm_availability_changed", lk_llm.AvailabilityChangedEvent(primary, True))
    assert len(switches) == 1


def test_the_voice_follows_language_and_style_on_every_instance() -> None:
    first, second = SilentTTS(), SilentTTS()
    voice = fallback.tts(first, [second], False, PAIR)
    assert isinstance(voice, fallback.FailoverTTS)
    voice.speak_in("kn-IN")
    voice.style("greeting")
    assert first.languages == second.languages == ["kn-IN"]
    assert first.situations == second.situations == ["greeting"]
