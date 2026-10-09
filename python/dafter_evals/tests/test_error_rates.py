from __future__ import annotations

import pytest
from dafter_evals.accuracy import entity_heard, primary_metric
from dafter_evals.wer import (
    character_error_rate,
    characters,
    error_rates,
    orthography_aware_error_rate,
    spelling_lattice,
    word_error_rate,
)


def test_characters_are_counted_after_normalisation_without_spaces() -> None:
    assert characters("Ab, c") == ["a", "b", "c"]
    assert character_error_rate("हाँ ज़रा", "हां जरा").errors == 0
    assert character_error_rate("ab cd", "abcd").errors == 0


def test_a_character_error_rate_counts_each_kind_of_edit() -> None:
    result = character_error_rate("abc", "abd")
    assert (result.reference_length, result.substitutions, result.rate) == (3, 1, 1 / 3)
    assert character_error_rate("abc", "ab").deletions == 1
    assert character_error_rate("abc", "abcd").insertions == 1


def test_a_vowel_sign_is_one_character_wrong_not_one_word_wrong() -> None:
    result = character_error_rate("मीटिंग", "मिटिंग")
    assert (result.reference_length, result.substitutions) == (6, 1)
    assert word_error_rate("मीटिंग", "मिटिंग").wer == 1.0


def test_an_empty_reference_has_no_character_rate() -> None:
    with pytest.raises(ValueError):
        character_error_rate(" ।", "a")


def test_either_valid_spelling_of_a_word_is_accepted() -> None:
    references = ["मेरा फोटो भेजो", "mera photo bhejo"]
    assert word_error_rate(references[0], "मेरा photo भेजो").substitutions == 1
    result = orthography_aware_error_rate(references, "मेरा photo भेजो")
    assert (result.errors, result.reference_length) == (0, 3)
    assert orthography_aware_error_rate(references, "Mera Photo Bhejo").errors == 0
    assert orthography_aware_error_rate(references, "मेरा वीडियो भेजो").substitutions == 1


def test_a_spelling_with_another_word_count_is_scored_on_its_own() -> None:
    lattices = spelling_lattice(["ठीक है", "theek hai ji", "thik hai"])
    assert lattices[0] == [frozenset({"ठीक", "thik"}), frozenset({"है", "hai"})]
    assert len(lattices) == 2
    result = orthography_aware_error_rate(["ठीक है", "theek hai ji"], "theek hai ji")
    assert (result.errors, result.reference_length) == (0, 3)
    with pytest.raises(ValueError):
        orthography_aware_error_rate(["।"], "a")


def test_all_three_rates_are_reported_and_oiwer_never_exceeds_wer() -> None:
    metrics = error_rates(["मेरा फोटो भेजो", "mera photo bhejo"], "मेरा photo भेजो")
    report = metrics.to_dict()
    assert report["wer"] == round(1 / 3, 4) and report["oiwer"] == 0.0
    assert report["referenceCharacters"] == len(characters("मेरा फोटो भेजो"))
    assert report["characterErrors"] == metrics.cer.errors > 0
    with pytest.raises(ValueError):
        error_rates([], "a")


@pytest.mark.parametrize(
    ("entity", "hypothesis", "heard"),
    [
        ("98765 43210", "मेरा नंबर 9876543210 है", True),
        ("9876543210", "nine 98765-43210 call", True),
        ("२५ मार्च", "25 मार्च को आना", True),
        ("₹500", "500 रुपये दे दो", True),
        ("Ravi Kumar", "ravi kumar ji", True),
        ("Ravi Kumar", "ravi kumaar ji", False),
        ("50", "500 रुपये", False),
        ("ಬೆಂಗಳೂರು", "ನಾನು ಬೆಂಗಳೂರು ಇಂದ", True),
    ],
)
def test_an_entity_is_heard_only_when_its_words_appear_exactly(
    entity: str, hypothesis: str, heard: bool
) -> None:
    assert entity_heard(entity, hypothesis) is heard


def test_an_entity_without_words_cannot_be_scored() -> None:
    with pytest.raises(ValueError):
        entity_heard("।", "a")


@pytest.mark.parametrize(
    ("language", "metric"),
    [("en-IN", "wer"), ("hi", "wer"), ("mr-IN", "wer"), ("te-IN", "cer"), ("kn-IN", "cer")],
)
def test_each_language_is_gated_on_its_primary_metric(language: str, metric: str) -> None:
    assert primary_metric(language) == metric
