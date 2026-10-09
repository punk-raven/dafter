from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from dafter_evals.screen import __main__ as cli
from dafter_evals.screen import catalog as catalogs
from dafter_evals.screen.report import Record, Row, gate, merged, rank, sample, table

CATALOG = catalogs.load()
PICK = {c.id: c for c in CATALOG.candidates}


def rec(candidate: str, n: int, **fields: Any) -> Record:
    c = PICK[candidate]
    base = Record(
        date="2026-09-27",
        candidate=c.id,
        role=c.role,
        provider=c.ref.provider,
        model=c.ref.model or "",
        language="hi",
        question_id=f"hi-{n:02d}",
        question="q",
        run=1,
        reply="नमस्ते।",
        ttft_ms=100,
        ttfs_ms=200,
        total_ms=300,
        input_tokens=50,
        output_tokens=7,
        reasoning_tokens=0,
        cost=0.00001,
        currency="USD",
        cost_inr=0.0009582,
        free_tier=c.free_tier,
        tool_calls=0,
        markdown=False,
        digits=False,
        verdicts={"correctness": "pass", "language": "pass"},
        score=1.0,
    )
    return replace(base, **fields)


def rows() -> list[Row]:
    return [
        Row(PICK["openai_mini"], [rec("openai_mini", n, ttfs_ms=150) for n in range(10)]),
        Row(PICK["gemini_flash"], [rec("gemini_flash", n, ttfs_ms=400) for n in range(10)]),
        Row(PICK["gemini_flash_lite"], [rec("gemini_flash_lite", n, score=0.5) for n in range(10)]),
        Row(PICK["sarvam_105b"], [], skipped="authentication_failed: no key"),
    ]


def test_quality_ranks_first_then_time_to_first_sentence_and_skips_go_last() -> None:
    order = [r.candidate.id for r in rank(rows())]
    assert order == ["openai_mini", "gemini_flash", "gemini_flash_lite", "sarvam_105b"]


def test_the_table_names_date_judge_and_every_candidate() -> None:
    text = table(rows(), "2026-09-27", "judge_gemini_flash (a-model)", ["hi", "kn"])
    lines = text.splitlines()
    assert lines[0] == (
        "Stage 4 LLM screen, 2026-09-27, languages: hi, kn, judge: judge_gemini_flash (a-model)"
    )
    assert lines[4].startswith("| 1 | openai_mini | openai/gpt-5.4-mini")
    assert "| 150/150 |" in lines[4] and "| 0.9582 |" in lines[4] and "not stated" in lines[4]
    assert "skipped: authentication_failed: no key" in lines[-1]


def test_errors_count_against_answered_and_are_left_out_of_latency() -> None:
    records = [rec("gemini_flash", 0), rec("gemini_flash", 1, error="rate_limited", reply=None)]
    s = Row(PICK["gemini_flash"], records).summary()
    assert (s["calls"], s["answered"], s["rateLimited"], s["ttfsP95Ms"]) == (2, 1, 1, 200)
    assert s["passRates"]["correctness"] == 1.0 and s["passRates"]["register"] is None


def test_the_spot_check_sample_is_a_fifth_of_answered_replies_and_repeatable() -> None:
    records = [r for row in rows() for r in row.records]
    records.append(rec("gemini_flash", 99, error="rate_limited", reply=None))
    first, again = sample(records, seed=7), sample(records, seed=7)
    assert len(first) == 6 and first == again
    assert all(s["reply"] is not None for s in first)
    assert sample(records, seed=8) != first
    assert sample([], seed=7) == []


def test_rows_from_several_languages_merge_per_candidate_in_first_seen_order() -> None:
    hindi = rows()
    kannada = [
        Row(PICK["gemini_flash"], [rec("gemini_flash", n, language="kn") for n in range(3)]),
        Row(PICK["sarvam_105b"], [], skipped="authentication_failed: no key"),
    ]
    together = merged([hindi, kannada])
    assert [r.candidate.id for r in together] == [r.candidate.id for r in hindi]
    by = {r.candidate.id: r for r in together}
    assert len(by["gemini_flash"].records) == 13
    assert {r.language for r in by["gemini_flash"].records} == {"hi", "kn"}
    assert by["sarvam_105b"].skipped == "authentication_failed: no key"
    assert len(hindi[1].records) == 10


def test_a_row_without_quality_scores_is_listed_but_not_numbered() -> None:
    unjudged = [
        Row(
            PICK["openai_mini"], [rec("openai_mini", n, score=None, verdicts={}) for n in range(3)]
        ),
        Row(PICK["gemini_flash"], [rec("gemini_flash", n) for n in range(3)]),
        Row(PICK["gemini_flash_lite"], [rec("gemini_flash_lite", n, score=None) for n in range(3)]),
    ]
    lines = table(unjudged, "2026-09-28", None, ["hi"]).splitlines()[4:]
    assert [line.split(" | ")[:2] for line in lines] == [
        ["| 1", "gemini_flash"],
        ["| -", "openai_mini"],
        ["| -", "gemini_flash_lite"],
    ]


def test_after_a_judge_ran_the_sample_is_drawn_from_judged_replies_only() -> None:
    judged = [rec("gemini_flash", n) for n in range(10)]
    failed = [
        rec("gemini_flash", 10 + n, verdicts={}, judge_error="rate_limited") for n in range(10)
    ]
    picked = sample([*judged, *failed], seed=7, judged=True)
    assert len(picked) == 2
    assert all(s["judge"]["verdicts"] for s in picked)
    assert len(sample([*judged, *failed], seed=7)) == 4
    assert sample(failed, seed=7, judged=True) == []


def test_the_gate_passes_only_when_every_candidate_meets_every_criterion() -> None:
    clean = [Row(PICK["openai_mini"], [rec("openai_mini", n) for n in range(3)])]
    assert gate(clean) == {"go": True, "complete": True, "misses": {}, "incomplete": {}}
    assert gate([])["go"] is False


def test_quality_misses_fail_and_unanswered_transient_calls_leave_the_run_incomplete() -> None:
    probe = {"expected_tools": ["lookup_order"], "missed_tools": ["lookup_order"]}
    flawed = [
        rec("gemini_flash", 0, error="rate_limited", reply=None, transient=True, attempts=4),
        rec("gemini_flash", 1, verdicts={"correctness": "fail", "language": "maybe"}),
        rec("gemini_flash", 2, **probe),
    ]
    verdict = gate([*rows()[:3], Row(PICK["gemini_flash"], flawed)])
    assert (verdict["go"], verdict["complete"]) == (False, False)
    assert verdict["misses"] == {
        "gemini_flash": ["1 of 1 tool probes not clean", "correctness failed on 1 replies"]
    }
    assert verdict["incomplete"] == {"gemini_flash": ["1 of 3 calls unanswered after retries"]}
    summary = Row(PICK["gemini_flash"], flawed).summary()
    assert (summary["retriedCalls"], summary["incomplete"]) == (
        1,
        verdict["incomplete"]["gemini_flash"],
    )


def test_a_skipped_candidate_leaves_the_run_incomplete_not_failed() -> None:
    verdict = gate(rows())
    assert verdict["misses"] == {}
    assert verdict["incomplete"] == {"sarvam_105b": ["not screened: authentication_failed: no key"]}


def test_a_call_the_provider_refused_outright_is_a_candidate_miss() -> None:
    refused = [rec("gemini_flash", 0, error="bad_request", reply=None), rec("gemini_flash", 1)]
    verdict = gate([Row(PICK["gemini_flash"], refused)])
    assert verdict["misses"] == {"gemini_flash": ["1 of 2 calls refused by the provider"]}
    assert verdict["complete"] is True


@pytest.mark.parametrize(
    ("misses", "incomplete", "report_only", "code"),
    [
        ({}, {}, False, 0),
        ({"a": ["x"]}, {}, False, 1),
        ({"a": ["x"]}, {"b": ["y"]}, False, 1),
        ({}, {"b": ["y"]}, False, 2),
        ({"a": ["x"]}, {"b": ["y"]}, True, 0),
    ],
)
def test_the_exit_code_tells_a_miss_from_an_incomplete_run(
    misses: dict[str, list[str]], incomplete: dict[str, list[str]], report_only: bool, code: int
) -> None:
    verdict = {"go": not misses and not incomplete, "misses": misses, "incomplete": incomplete}
    assert cli.exit_code(verdict, report_only) == code
