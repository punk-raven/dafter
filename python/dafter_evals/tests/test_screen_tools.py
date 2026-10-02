from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
from dafter_core.config import ProviderRef
from dafter_evals.screen import __main__ as cli
from dafter_evals.screen import bank as banks
from dafter_evals.screen import catalog as catalogs
from dafter_evals.screen.judge import Judge
from dafter_evals.screen.report import Record, Row
from dafter_evals.screen.run import Screen, Settings
from dafter_evals.screen.tools import NAMES, TOOLS
from dafter_evals.screen.turn import Classify
from dafter_providers import openai_compat
from livekit.agents import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions, llm
from livekit.agents.llm.tool_context import get_fnc_tool_names
from livekit.agents.types import NOT_GIVEN, NotGivenOr

classify: Classify = openai_compat.classify
Step = list[tuple[str, str]] | str
SCORES = {
    "correctness": "pass",
    "language": "pass",
    "register": "pass",
    "speakability": "pass",
    "reasoning": "fine",
}


class ScriptedStream(llm.LLMStream):
    def __init__(
        self,
        owner: Scripted,
        *,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(owner, chat_ctx=chat_ctx, tools=tools, conn_options=conn_options)
        self._owner = owner

    async def _run(self) -> None:
        owner = self._owner
        offered = get_fnc_tool_names(self._tools)
        owner.offered.append(offered)
        for name, args in owner.answers.items():
            if name in offered:
                call = llm.FunctionToolCall(name=name, arguments=args, call_id="judge")
                self._send(llm.ChoiceDelta(role="assistant", tool_calls=[call]))
                return
        step = owner.steps.pop(0) if owner.steps else "ठीक है।"
        if isinstance(step, str):
            self._send(llm.ChoiceDelta(role="assistant", content=step))
            return
        owner.made += 1
        calls = [
            llm.FunctionToolCall(name=name, arguments=args, call_id=f"t{owner.made}-{n}")
            for n, (name, args) in enumerate(step)
        ]
        self._send(llm.ChoiceDelta(role="assistant", tool_calls=calls))

    def _send(self, delta: llm.ChoiceDelta) -> None:
        self._event_ch.send_nowait(llm.ChatChunk(id="scripted", delta=delta))


class Scripted(llm.LLM[Any]):
    def __init__(self, *steps: Step, answers: dict[str, str] | None = None) -> None:
        super().__init__()
        self.steps = list(steps)
        self.answers = answers or {}
        self.offered: list[list[str]] = []
        self.made = 0

    @property
    def model(self) -> str:
        return "scripted"

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
        return ScriptedStream(self, chat_ctx=chat_ctx, tools=tools or [], conn_options=conn_options)


def weather(city: str) -> tuple[str, str]:
    return ("get_weather", json.dumps({"city": city}))


def probe(question_id: str) -> banks.Bank:
    hindi = banks.load("hi")
    picked = tuple(q for q in hindi.questions if q.id == question_id)
    return banks.Bank("hi", "Hindi", "Devanagari", "आप", picked)


def screened(question_id: str, model: Scripted, judge: Scripted | None = None) -> Row:
    def build(ref: ProviderRef) -> tuple[llm.LLM[Any], Classify]:
        return model, classify

    bank = probe(question_id)
    graded = Judge(judge, classify, bank, 5.0) if judge else None
    settings = Settings(date="2026-09-28", runs=1)
    s = Screen(settings, bank, "Reply in Hindi.", graded, build, say=lambda _: None, tools=[*TOOLS])
    rows = asyncio.run(s.run(catalogs.load().pick(["gemini_flash_lite"])))
    return rows[0]


def only(row: Row) -> Record:
    assert len(row.records) == 1
    return row.records[0]


def test_the_screen_offers_exactly_the_tools_its_probes_name() -> None:
    assert set(get_fnc_tool_names(list(TOOLS))) == NAMES
    probes = {q.id: q.tools for q in banks.load("hi").questions if q.tools}
    assert probes == {
        "hi-21": ("get_weather",),
        "hi-22": ("get_weather", "get_weather"),
        "hi-23": ("end_call",),
    }


def test_two_calls_in_one_response_are_recorded_as_parallel() -> None:
    model = Scripted([weather("दिल्ली"), weather("मुंबई")], "दोनों जगह इकतीस डिग्री और हल्की बारिश है।")
    row = screened("hi-22", model)
    rec = only(row)
    assert rec.tool_responses == [["get_weather", "get_weather"], []]
    assert (rec.tool_calls, rec.parallel_tool_calls, rec.missed_tools) == (2, True, [])
    assert (rec.tool_quoted, rec.claimed_without_call, rec.tools_clean) == (True, False, False)
    assert row.summary()["parallelToolCalls"] == 1
    assert (row.summary()["toolProbes"], row.summary()["toolProbesClean"]) == (1, 0)


def test_one_call_at_a_time_that_misquotes_the_result_is_caught() -> None:
    model = Scripted([weather("दिल्ली")], [weather("मुंबई")], "दोनों जगह बत्तीस डिग्री है।")
    rec = only(screened("hi-22", model))
    assert rec.tool_responses == [["get_weather"], ["get_weather"], []]
    assert (rec.parallel_tool_calls, rec.tool_quoted, rec.tools_clean) == (False, False, False)
    assert model.offered[0] == ["get_weather", "end_call"]


def test_a_quoted_single_lookup_is_clean() -> None:
    rec = only(screened("hi-21", Scripted([weather("दिल्ली")], "आज दिल्ली में इकतीस डिग्री है।")))
    assert (rec.tool_quoted, rec.claimed_without_call, rec.tools_clean) == (True, False, True)


@pytest.mark.parametrize(
    ("question_id", "reply", "claimed"),
    [
        ("hi-21", "दिल्ली में आज अड़तीस डिग्री है और धूप खिली है।", True),
        ("hi-21", "मैं चेक कर रही हूँ, एक मिनट रुकिए।", True),
        ("hi-21", "माफ़ कीजिए, मैं अभी मौसम नहीं जान सकती।", False),
        ("hi-22", "मौसम देखने के लिए मुझे थोड़ा समय चाहिए। क्या आप कुछ मिनट दे सकते हैं?", True),
        ("hi-23", "ठीक है, मैं कॉल काट रही हूँ।", True),
    ],
)
def test_a_lookup_or_action_claimed_without_a_call_is_flagged(
    question_id: str, reply: str, claimed: bool
) -> None:
    row = screened(question_id, Scripted(reply))
    rec = only(row)
    assert rec.tool_calls == 0 and rec.missed_tools and rec.tool_quoted is None
    assert rec.claimed_without_call is claimed and not rec.tools_clean
    assert row.summary()["claimedWithoutCall"] == int(claimed)


def test_a_call_on_a_question_that_needs_none_is_unexpected() -> None:
    rec = only(screened("hi-01", Scripted([("end_call", "{}")], "नमस्ते।")))
    assert (rec.unexpected_tools, rec.claimed_without_call) == (["end_call"], None)


def test_the_tool_use_judge_grades_a_probe_even_when_no_tool_was_called() -> None:
    verdict = json.dumps({"verdict": "fail", "reasoning": "it never looked the weather up"})
    judge = Scripted(answers={"submit_scores": json.dumps(SCORES), "submit_verdict": verdict})
    rec = only(screened("hi-21", Scripted("दिल्ली में अड़तीस डिग्री है।"), judge))
    assert (rec.tool_calls, rec.tool_use, rec.score) == (0, "fail", 1.0)


def test_the_cli_offers_the_fixed_tool_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    model = Scripted()

    def build(ref: ProviderRef) -> tuple[llm.LLM[Any], Classify]:
        return model, classify

    monkeypatch.setattr(cli, "build", build)
    argv = ["--out", str(tmp_path), "--runs", "1", "--candidates", "gemini_flash_lite"]
    with pytest.raises(SystemExit) as exited:
        cli.main([*argv, "--no-judge"])
    assert exited.value.code == 0
    assert all(offered == ["get_weather", "end_call"] for offered in model.offered)
    results = (tmp_path / "results.jsonl").read_text(encoding="utf-8").splitlines()
    probes = [json.loads(r) for r in results if json.loads(r)["expected_tools"]]
    assert [p["question_id"] for p in probes] == ["hi-21", "hi-22", "hi-23"]
    assert all(p["missed_tools"] and p["tool_responses"] == [[]] for p in probes)
