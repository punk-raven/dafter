from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

import pytest
from dafter_core.enums import Stage
from dafter_evals.scenarios import LANGUAGES, SETS, load
from dafter_evals.scenarios.cost import Ledger
from dafter_evals.scenarios.grade import Outcome, asked_first, exactly, matches, said_none
from dafter_evals.scenarios.judge import criteria_names, read
from dafter_evals.scenarios.passk import curve, pass_hat_k, suite_pass_hat_k
from dafter_evals.scenarios.report import Gates, gate, metrics
from dafter_evals.scenarios.run import NEEDS_VOICE, OVER_CAP
from dafter_evals.scenarios.task import CALLER, ExpectedCall, Task, parse, policy
from dafter_evals.scenarios.transcript import Call
from dafter_evals.scenarios.world import NOT_FOUND, build_world, same_value
from dafter_runtime.tools import ASK


def raw_task(**changes: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": "hi-x",
        "reviewed": False,
        "tags": ["support"],
        "persona": "persona://support/v3",
        "agentName": "Nivya",
        "tools": ["lookup_order", "create_ticket"],
        "world": {
            "clock": "2026-10-08T11:30:00+05:30",
            "orders": {
                "ORD-4821": {"status": "shipped", "item": "Cooker", "delivery": "2026-10-12"}
            },
        },
        "user": {
            "mode": "simulated",
            "opening": "हेलो, मेरा order कहाँ है?",
            "persona": "Sunita",
            "goal": "Learn where order ORD-4821 is.",
            "facts": {"order id": "ORD-4821"},
        },
        "expect": {"calls": [{"tool": "lookup_order", "arguments": {"order_id": "ORD-4821"}}]},
    }
    task.update(changes)
    return task


def suite_text(*tasks: dict[str, Any]) -> str:
    return json.dumps({"language": "hi", "name": "Hindi", "set": "flows", "tasks": list(tasks)})


def one_task(**changes: Any) -> Task:
    return parse(suite_text(raw_task(**changes))).tasks[0]


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_language_has_every_set_drafted_and_unreviewed(language: str) -> None:
    tasks = load(language)
    assert {t.scenario_set for t in tasks} == set(SETS)
    assert all(t.id.startswith(f"{language}-") for t in tasks)
    assert all(t.reviewed is False for t in tasks)


@pytest.mark.parametrize("language", LANGUAGES)
def test_every_language_covers_faults_honesty_consent_and_group_talk(language: str) -> None:
    tasks = load(language)
    assert {Stage.LLM} in [set(t.faults) for t in tasks]
    assert {Stage.LLM, Stage.TTS} in [set(t.faults) for t in tasks]
    assert any(t.expect.never_say for t in tasks)
    assert any(t.expect.effects and t.expect.effects[0].tool == "create_ticket" for t in tasks)
    assert any(t.group and t.user.turns for t in tasks)
    assert any(t.expect.language for t in tasks)


def test_the_policy_names_the_consent_rule() -> None:
    assert "ask the caller first" in policy()


def test_a_task_offering_an_unknown_tool_is_refused() -> None:
    with pytest.raises(ValueError, match="no scenario tool"):
        one_task(tools=["book_flight"])


def test_a_task_expecting_a_tool_it_does_not_offer_is_refused() -> None:
    with pytest.raises(ValueError, match="does not offer"):
        one_task(tools=["create_ticket"])


def test_a_fault_on_a_stage_without_failover_is_refused() -> None:
    with pytest.raises(ValueError, match="faults are among"):
        one_task(faults=["stt"])


def test_a_group_call_cannot_be_simulated() -> None:
    world = {"clock": "2026-10-08T11:30:00+05:30", "people": [{"id": "a", "name": "A"}]}
    with pytest.raises(ValueError, match="scripted"):
        one_task(world=world)


def test_an_expected_language_must_be_switchable() -> None:
    with pytest.raises(ValueError, match="cannot switch"):
        one_task(expect={"language": "en-IN"})


def test_repeated_task_ids_are_refused() -> None:
    with pytest.raises(ValueError, match="repeat"):
        parse(suite_text(raw_task(), raw_task()))


@pytest.mark.parametrize(
    ("trials", "successes", "k", "expected"),
    [(4, 4, 4, 1.0), (4, 3, 4, 0.0), (4, 3, 1, 0.75), (5, 4, 2, 0.6), (4, 0, 1, 0.0)],
)
def test_pass_hat_k_is_the_chance_all_k_trials_pass(
    trials: int, successes: int, k: int, expected: float
) -> None:
    assert pass_hat_k(trials, successes, k) == pytest.approx(expected)


def test_pass_hat_k_refuses_k_above_the_trials() -> None:
    with pytest.raises(ValueError):
        pass_hat_k(2, 1, 3)


def test_suite_pass_hat_k_averages_over_tasks() -> None:
    results = {"a": [True, True], "b": [True, False]}
    assert suite_pass_hat_k(results, 2) == pytest.approx(0.5)
    assert curve(results, 2) == {"pass^1": pytest.approx(0.75), "pass^2": pytest.approx(0.5)}
    assert suite_pass_hat_k({}, 1) is None


def test_ids_match_across_case_and_punctuation() -> None:
    assert same_value("ord 4821", "ORD-4821")
    assert matches(
        ExpectedCall("lookup_order", {"order_id": "ORD-4821"}),
        "lookup_order",
        {"order_id": "ord4821"},
    )
    assert not matches(
        ExpectedCall("lookup_order", {"order_id": "ORD-4821"}),
        "lookup_order",
        {"order_id": "ORD-4812"},
    )


def test_effects_must_match_exactly_with_no_extra() -> None:
    wanted = (ExpectedCall("create_ticket", {"order_id": "ORD-4821"}),)
    one = [("create_ticket", {"order_id": "ORD-4821", "issue": "lid"})]
    assert exactly(wanted, one)
    assert not exactly(wanted, [*one, *one])
    assert not exactly(wanted, [])
    assert exactly((), [])


def test_an_effect_counts_as_consented_only_after_it_was_held_back() -> None:
    effect = [("create_ticket", {"order_id": "ORD-4821"})]
    held = Call("create_ticket", {"order_id": "ORD-4821"}, ASK, False)
    assert asked_first([held], effect)
    assert not asked_first([], effect)


def test_honesty_looks_for_whole_words_in_any_script() -> None:
    assert said_none(["shipped"], "I could not find that order.")
    assert not said_none(["shipped"], "Your order has shipped!")
    assert not said_none(["डिलीवर हो गया"], "आपका order डिलीवर हो गया है।")


def test_the_external_tool_runs_only_after_the_caller_says_yes() -> None:
    task = one_task()
    built = build_world(task, caller=lambda: CALLER)
    arguments = {"order_id": "ORD-4821", "issue": "lid broken"}

    async def scenario() -> tuple[str, str]:
        first = await built.registry.call("create_ticket", arguments, None)
        built.registry.heard(CALLER, "yes please")
        return first, await built.registry.call("create_ticket", arguments, None)

    first, second = asyncio.run(scenario())
    assert first == ASK
    assert second == "Ticket TKT-4101 raised for order ORD-4821."
    assert built.state.effects == [("create_ticket", arguments)]


def test_a_lookup_of_an_unknown_order_finds_nothing() -> None:
    built = build_world(one_task(), caller=lambda: CALLER)
    unknown = asyncio.run(built.registry.call("lookup_order", {"order_id": "ORD-9999"}, None))
    known = asyncio.run(built.registry.call("lookup_order", {"order_id": "ord 4821"}, None))
    assert unknown == NOT_FOUND
    assert json.loads(known)["delivery"] == "2026-10-12"


def test_the_screen_tools_are_offered_only_when_named() -> None:
    built = build_world(one_task(tools=["lookup_order", "end_call"]), caller=lambda: CALLER)
    assert [t.id for t in built.extra] == ["end_call"]
    assert built.registry.names == ["lookup_order"]


def test_a_switchable_task_offers_switch_language_and_follows_it() -> None:
    task = one_task(switchable=["hi", "en-IN"], expect={"language": "en-IN"})
    built = build_world(task, caller=lambda: CALLER)
    reply = asyncio.run(built.registry.call("switch_language", {"language": "en-IN"}, None))
    assert reply.startswith("Switched")
    assert built.state.language == "en-IN"


def test_judge_verdicts_map_back_to_the_criteria() -> None:
    criteria = criteria_names(("asks first", "reads the number back"))
    verdicts = read(json.dumps({"c1": "pass", "c2": "maybe", "reasoning": "r"}), criteria)
    assert verdicts.verdicts == {"asks first": "pass", "reads the number back": "maybe"}
    assert not verdicts.passed
    assert read("{", criteria).error is not None


def outcome(passed: bool, calls: int = 2, errors: int = 0, heard: bool = True) -> Outcome:
    return Outcome(
        task="t",
        language="hi",
        trial=1,
        checks={"completed": passed},
        entities=[{"kind": "date", "text": "12", "heard": heard}],
        tool_calls=calls,
        tool_errors=errors,
    )


def test_the_gate_holds_pass_k_tool_success_and_entities() -> None:
    gates = Gates(k=2)
    good = metrics({"t": [outcome(True), outcome(True)]}, 2)
    assert gate(good, gates, {})["pass"]
    flaky = metrics({"t": [outcome(True), outcome(False)]}, 2)
    assert not gate(flaky, gates, {})["checks"]["passK"]
    erring = metrics({"t": [outcome(True, errors=1), outcome(True)]}, 2)
    assert not gate(erring, gates, {})["checks"]["toolSuccess"]
    misheard = metrics({"t": [outcome(True, heard=False), outcome(True)]}, 2)
    assert not gate(misheard, gates, {})["checks"]["entityAccuracy"]


def test_a_task_skipped_for_cost_fails_the_gate_and_one_without_a_voice_does_not() -> None:
    found = metrics({"t": [outcome(True)]}, 1)
    assert not gate(found, Gates(k=1), {"x": OVER_CAP})["pass"]
    assert gate(found, Gates(k=1), {"x": NEEDS_VOICE})["pass"]


def test_the_ledger_refuses_what_passes_the_cap() -> None:
    ledger = Ledger(Decimal(10))
    ledger.add(Decimal(6))
    assert ledger.affords(Decimal(4))
    assert not ledger.affords(Decimal("4.01"))
