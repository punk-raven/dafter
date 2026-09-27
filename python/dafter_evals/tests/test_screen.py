from __future__ import annotations

import asyncio
import itertools
import json
from pathlib import Path
from typing import Any

import pytest
from dafter_core.config import ProviderRef
from dafter_core.enums import ErrorCode
from dafter_core.errors import DafterError
from dafter_evals.screen import __main__ as cli
from dafter_evals.screen import bank as banks
from dafter_evals.screen import catalog as catalogs
from dafter_evals.screen.judge import Judge, digits, markdown
from dafter_evals.screen.run import Screen, Settings
from dafter_evals.screen.turn import Classify, ask
from dafter_evals.script import HINDI
from dafter_providers import openai_compat
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    function_tool,
    llm,
)
from livekit.agents.llm.tool_context import get_fnc_tool_names
from livekit.agents.types import NOT_GIVEN, NotGivenOr

INSTRUCTIONS = "Reply in Hindi."
SCORES = {
    "correctness": "pass",
    "language": "pass",
    "register": "maybe",
    "speakability": "pass",
    "reasoning": "fine",
}
VERDICT = {"verdict": "pass", "reasoning": "the tool was used well"}
classify: Classify = openai_compat.classify
Llm = llm.LLM[Any]


class StubStream(llm.LLMStream):
    def __init__(
        self,
        owner: Stub,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(owner, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._owner = owner

    def _send(self, delta: llm.ChoiceDelta) -> None:
        self._event_ch.send_nowait(llm.ChatChunk(id="stub", delta=delta))

    async def _run(self) -> None:
        owner = self._owner
        owner.calls += 1
        if owner.error is not None:
            raise owner.error
        for name in get_fnc_tool_names(self._tools):
            if name in owner.judge:
                args = owner.judge[name]
                call = llm.FunctionToolCall(name=name, arguments=args, call_id=f"c{owner.calls}")
                self._send(llm.ChoiceDelta(role="assistant", tool_calls=[call]))
                return
        if owner.tool_calls and owner.calls == 1:
            self._send(llm.ChoiceDelta(role="assistant", tool_calls=owner.tool_calls))
            return
        for piece in owner.chunks:
            self._send(llm.ChoiceDelta(role="assistant", content=piece))
        usage = llm.CompletionUsage(completion_tokens=7, prompt_tokens=50, total_tokens=57)
        self._event_ch.send_nowait(llm.ChatChunk(id="stub", usage=usage))


class Stub(llm.LLM[Any]):
    def __init__(
        self,
        chunks: tuple[str, ...] = ("नमस्ते", "! आप कैसे हैं?"),
        error: BaseException | None = None,
        judge: dict[str, str] | None = None,
        tool_calls: list[llm.FunctionToolCall] | None = None,
    ) -> None:
        super().__init__()
        self.chunks = chunks
        self.error = error
        self.judge = judge or {}
        self.tool_calls = tool_calls or []
        self.calls = 0
        self.prompts: list[str] = []

    @property
    def model(self) -> str:
        return "stub-model"

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
        self.prompts.extend(m.text_content or "" for m in chat_ctx.messages())
        return StubStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


def judge_stub(scores: dict[str, str] | None = None) -> Stub:
    return Stub(
        judge={
            "submit_scores": json.dumps(scores or SCORES),
            "submit_verdict": json.dumps(VERDICT),
        }
    )


def ticks() -> Any:
    counter = itertools.count()
    return lambda: next(counter) * 0.1


def hindi() -> banks.Bank:
    return banks.load("hi")


def test_the_hindi_bank_is_the_scripted_hindi_turns() -> None:
    bank = hindi()
    assert tuple(q.text for q in bank.questions) == HINDI.turns
    assert (bank.script, bank.register) == ("Devanagari", "आप")


@pytest.mark.parametrize("language", ["kn", "en", "mr", "te"])
def test_other_languages_have_empty_banks_that_say_so(language: str) -> None:
    bank = banks.load(language)
    assert bank.empty
    assert f"banks/{language}.json" in bank.refusal()
    assert "never generates" in bank.refusal()


def test_an_unknown_language_has_no_bank() -> None:
    with pytest.raises(ValueError, match="no question bank"):
        banks.load("fr")


def test_a_reply_is_timed_to_its_first_token_and_first_sentence() -> None:
    reply = asyncio.run(ask(Stub(), classify, INSTRUCTIONS, "नमस्ते", 5.0, ticks()))
    assert reply.error is None
    assert reply.text == "नमस्ते! आप कैसे हैं?"
    assert (reply.ttft_ms, reply.ttfs_ms, reply.total_ms) == (100, 200, 300)
    assert (reply.input_tokens, reply.output_tokens, reply.tool_calls) == (50, 7, 0)


def test_a_reply_without_a_sentence_end_is_one_sentence() -> None:
    stub = Stub(chunks=("हाँ", " जी"))
    reply = asyncio.run(ask(stub, classify, INSTRUCTIONS, "ठीक है", 5.0, ticks()))
    assert (reply.ttft_ms, reply.ttfs_ms) == (100, 300)
    assert stub.prompts[:2] == [INSTRUCTIONS, "ठीक है"]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (APIStatusError("slow down", status_code=429), ErrorCode.RATE_LIMITED),
        (APIStatusError("no credits", status_code=402), ErrorCode.QUOTA_EXCEEDED),
        (APIStatusError("bad key", status_code=401), ErrorCode.AUTHENTICATION_FAILED),
        (APITimeoutError(), ErrorCode.PROVIDER_TIMEOUT),
    ],
)
def test_a_failed_reply_is_classified_not_raised(error: BaseException, code: ErrorCode) -> None:
    reply = asyncio.run(ask(Stub(error=error), classify, INSTRUCTIONS, "नमस्ते", 5.0, ticks()))
    assert reply.error is not None and reply.error.code is code
    assert (reply.text, reply.ttfs_ms) == (None, None)


def test_the_judge_gives_a_verdict_per_criterion() -> None:
    stub = judge_stub()
    scores = asyncio.run(Judge(stub, classify, hindi(), 5.0).score("नमस्ते", "नमस्ते।"))
    assert scores.error is None
    assert scores.verdicts == {k: v for k, v in SCORES.items() if k != "reasoning"}
    assert scores.score() == 0.875
    prompt = stub.prompts[-1]
    assert "Hindi, written in Devanagari script" in prompt and "आप" in prompt


def test_a_bank_without_a_register_is_not_judged_on_one() -> None:
    bank = banks.Bank("xx", "Test", None, None, (banks.Question("xx-01", "q"),))
    scores = {k: v for k, v in SCORES.items() if k != "register"}
    judged = asyncio.run(Judge(judge_stub(scores), classify, bank, 5.0).score("q", "a"))
    assert set(judged.verdicts) == {"correctness", "language", "speakability"}


@pytest.mark.parametrize(
    ("stub", "error"),
    [
        (Stub(error=APIStatusError("slow", status_code=429)), "rate_limited"),
        (judge_stub({"correctness": "great", "reasoning": ""}), "no verdict for"),
        (Stub(judge={"submit_scores": "not json"}), "not JSON"),
        (Stub(chunks=("no tool",)), "no scoring tool"),
    ],
)
def test_a_judge_that_fails_is_recorded_not_raised(stub: Stub, error: str) -> None:
    scores = asyncio.run(Judge(stub, classify, hindi(), 5.0).score("q", "a"))
    assert scores.error is not None and error in scores.error
    assert scores.score() is None


def test_unspeakable_replies_are_flagged_without_a_judge() -> None:
    assert markdown("**नमस्ते**") and markdown("सुझाव:\n- पानी पिएँ") and markdown("1. पहला")
    assert not markdown("नमस्ते, आप कैसे हैं?")
    assert digits("दो 2") and digits("२ गिलास") and not digits("दो गिलास")


def screen(
    stubs: dict[str, llm.LLM[Any]],
    runs: int = 1,
    judge: Llm | None = None,
    tools: list[llm.Tool] | None = None,
) -> tuple[list[Any], list[str]]:
    said: list[str] = []

    def build(ref: ProviderRef) -> tuple[llm.LLM[Any], Classify]:
        found = stubs.get(ref.model or "")
        if found is None:
            raise DafterError(ErrorCode.AUTHENTICATION_FAILED, "no key in the environment")
        return found, classify

    bank = banks.Bank("hi", "Hindi", "Devanagari", "आप", hindi().questions[:2])
    graded = Judge(judge, classify, bank, 5.0) if judge else None
    settings = Settings(date="2026-09-27", runs=runs)
    s = Screen(settings, bank, INSTRUCTIONS, graded, build, ticks(), said.append, tools)
    candidates = catalogs.load().pick(["gemini_flash_lite", "gemini_flash", "openai_mini"])
    return asyncio.run(s.run(candidates)), said


def test_the_screen_records_rate_limits_and_skips_a_candidate_it_cannot_build() -> None:
    limited = Stub(error=APIStatusError("slow", status_code=429))
    rows, said = screen({"gemini-3.5-flash-lite": Stub(), "gemini-3.6-flash": limited}, runs=2)
    by = {row.candidate.id: row for row in rows}
    ok, slow, skipped = by["gemini_flash_lite"], by["gemini_flash"], by["openai_mini"]
    assert ok.summary()["answered"] == 4 and ok.summary()["ttfsP50Ms"] == 200
    assert (slow.summary()["rateLimited"], slow.summary()["answered"]) == (4, 0)
    assert skipped.skipped is not None and "no key" in skipped.skipped
    assert skipped.records == [] and any("openai_mini: skipped" in s for s in said)
    record = ok.records[0]
    assert (record.date, record.provider, record.model) == (
        "2026-09-27",
        "openai_compat",
        "gemini-3.5-flash-lite",
    )
    assert record.cost == pytest.approx((50 * 0.3 + 7 * 2.5) / 1_000_000)
    assert slow.records[0].native_code == "429"


def test_a_tool_call_is_graded_by_the_livekit_tool_use_judge() -> None:
    call = llm.FunctionToolCall(name="lookup_order", arguments="{}", call_id="t1")
    stubs: dict[str, Llm] = {"gemini-3.5-flash-lite": Stub(tool_calls=[call])}
    rows, _ = screen(stubs, judge=judge_stub(), tools=[lookup_order])
    record = rows[0].records[0]
    assert record.tool_calls == 1
    assert record.tool_use == "pass"
    assert record.verdicts["register"] == "maybe"


async def accept(raw_arguments: dict[str, object]) -> str:
    return "order found"


lookup_order = function_tool(
    accept,
    raw_schema={
        "name": "lookup_order",
        "description": "Look up an order.",
        "parameters": {"type": "object", "properties": {}},
    },
)


def run_cli(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    judge_model = catalogs.load().judge.ref.model
    stubs: dict[str, Llm] = {"gemini-3.5-flash-lite": Stub(), "gpt-5.4-mini-2026-03-17": Stub()}

    def build(ref: ProviderRef) -> tuple[llm.LLM[Any], Classify]:
        return (judge_stub() if ref.model == judge_model else stubs[ref.model or ""]), classify

    monkeypatch.setattr(cli, "build", build)
    with pytest.raises(SystemExit) as exited:
        cli.main(argv)
    return int(exited.value.code or 0)


def test_the_cli_writes_a_ranked_table_and_a_spot_check_sample(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["--out", str(tmp_path), "--runs", "2", "--candidates", "gemini_flash_lite,openai_mini"]
    assert run_cli(argv, monkeypatch) == 0
    results = (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()
    sample = (tmp_path / "spot-check.jsonl").read_text(encoding="utf-8").splitlines()
    assert (len(results), len(sample)) == (2 * 2 * 20, 16)
    assert json.loads(sample[0])["human"]["correctness"] is None
    summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    assert (summary["language"], summary["runs"], summary["questions"]) == ("hi", 2, 20)
    assert [r["candidate"] for r in summary["ranking"]] == ["gemini_flash_lite", "openai_mini"]
    table = capsys.readouterr().out
    assert table == (tmp_path / "ranking.md").read_text(encoding="utf-8")
    assert "judge: judge_gemini_flash" in table and "openai_compat/gpt-5.4-mini" in table


def test_the_cli_refuses_an_empty_bank(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_cli(["--out", str(tmp_path), "--language", "kn"], monkeypatch) == 2
    assert "Kannada (kn) question bank is empty" in capsys.readouterr().err
    assert not (tmp_path / "results.jsonl").exists()
