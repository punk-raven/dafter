from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest
from dafter_runtime.naming import Heard, Matcher, tokens, within_one_edit, words
from dafter_runtime.plan import load

ROOT = Path(__file__).resolve().parents[3] / "testdata"
JOB = ROOT / "agent" / "hindi-webrtc-job.json"
CASES: list[dict[str, Any]] = json.loads((ROOT / "addressing" / "utterances.json").read_text())
LANGUAGES = ("en", "hi", "mr", "kn", "te")


def catalog_matcher() -> Matcher:
    return Matcher.for_addressing(load(JOB.read_bytes().strip()).agent.addressing)


MATCHER = catalog_matcher()


@pytest.mark.parametrize(
    "case", CASES, ids=[f"{c['language']}-{c['heard']}-{i}" for i, c in enumerate(CASES)]
)
def test_the_matcher_hears_each_pinned_utterance(case: dict[str, Any]) -> None:
    assert MATCHER.hear(case["text"]) is Heard(case["heard"]), case["why"]


def test_every_language_pins_every_kind_of_utterance() -> None:
    kinds: dict[str, set[str]] = defaultdict(set)
    for case in CASES:
        kinds[case["language"]].add(case["heard"])
    assert {lang: kinds[lang] for lang in LANGUAGES} == {
        lang: {str(h) for h in Heard} for lang in LANGUAGES
    }


def test_normalisation_folds_case_width_and_joiners() -> None:
    assert words("NIVYA") == words("nivya") == ("nivya",)
    assert words("\uff2e\uff29\uff36\uff39\uff21") == ("nivya",)
    assert words("नि\u200dव्या") == words("निव्या")
    assert words("don\u2019t stop") == words("don't stop") == ("dont", "stop")
    assert words("  \u00abNivya\u00bb -- hi ") == ("nivya", "hi")


def test_clause_punctuation_marks_breaks_and_other_symbols_do_not() -> None:
    heard = tokens("ok Nivya, what's up? fine")
    assert [(t.word, t.break_before, t.break_after) for t in heard] == [
        ("ok", True, False),
        ("nivya", False, True),
        ("whats", True, False),
        ("up", False, True),
        ("fine", True, True),
    ]
    assert [t.break_after for t in tokens("निव्या। हाँ")] == [True, True]
    assert [t.break_after for t in tokens('ok "Nivya" - hi')] == [False, False, True]


@pytest.mark.parametrize(
    ("a", "b", "near"),
    [
        ("nivya", "nivya", True),
        ("nivya", "nivia", True),
        ("nivya", "niviya", True),
        ("nivya", "nvya", True),
        ("nivya", "navia", False),
        ("nivya", "nivyala", False),
        ("ನಿವ್ಯ", "ನಿವ್ಯಾ", True),
    ],
)
def test_within_one_edit(a: str, b: str, near: bool) -> None:
    assert within_one_edit(a, b) is near
    assert within_one_edit(b, a) is near


def test_a_near_miss_blocks_the_tolerance_but_not_the_name() -> None:
    strict = Matcher("Nivya", (), ())
    assert strict.hear("Navya, hello") is Heard.CALLED
    guarded = Matcher("Nivya", (), ("Navya",))
    assert guarded.hear("Navya, hello") is Heard.ASIDE
    assert guarded.hear("Nivya, hello") is Heard.CALLED


def test_a_short_name_is_matched_exactly() -> None:
    m = Matcher("Ava", (), ())
    assert m.hear("Ava, what time is it") is Heard.CALLED
    assert m.hear("Eva, what time is it") is Heard.ASIDE


def test_a_name_of_several_words_is_matched_as_a_sequence() -> None:
    m = Matcher("Mera Dost", (), ())
    assert m.hear("Mera Dost, what time is it") is Heard.CALLED
    assert m.hear("mera phone kahan hai") is Heard.ASIDE


def test_nothing_heard_is_aside() -> None:
    assert MATCHER.hear("") is Heard.ASIDE
    assert MATCHER.hear("...") is Heard.ASIDE
