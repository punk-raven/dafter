from __future__ import annotations

import json
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from dafter_core.speech import Speech
from dafter_runtime import spoken_english, spoken_kannada, spoken_marathi, spoken_telugu
from dafter_runtime.speech_plan import NORMALIZERS, SpeechPlan

SPEECH = Path(__file__).resolve().parents[3] / "testdata" / "speech"
NORMALIZERS_BY_FILE: dict[str, Callable[[str], str]] = {
    "marathi": spoken_marathi.normalize,
    "telugu": spoken_telugu.normalize,
    "kannada": spoken_kannada.normalize,
    "english": spoken_english.normalize,
}


TWENTY_THREE_LAKH_ONWARDS = "twenty three lakh forty five thousand six hundred seventy eight"


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def golden(language: str) -> dict[str, Any]:
    document: dict[str, Any] = json.loads((SPEECH / f"{language}-normalization.json").read_bytes())
    return document


CASES = [
    pytest.param(language, case, id=f"{language}-{case['kind']}-{index}")
    for language in NORMALIZERS_BY_FILE
    for index, case in enumerate(golden(language)["cases"])
]


@pytest.mark.parametrize(("language", "case"), CASES)
def test_each_golden_pair_is_spoken_as_pinned(language: str, case: dict[str, str]) -> None:
    assert NORMALIZERS_BY_FILE[language](case["text"]) == nfc(case["spoken"])


@pytest.mark.parametrize("language", NORMALIZERS_BY_FILE)
def test_each_golden_file_says_whether_a_native_speaker_reviewed_it(language: str) -> None:
    assert isinstance(golden(language)["reviewed"], bool)


@pytest.mark.parametrize(
    ("words", "n", "spoken"),
    [
        (spoken_marathi.number_words, 0, "शून्य"),
        (spoken_marathi.number_words, 100, "शंभर"),
        (spoken_marathi.number_words, 199, "एकशे नव्व्याण्णव"),
        (spoken_marathi.number_words, 100000, "एक लाख"),
        (spoken_marathi.number_words, 10**9, "शंभर कोटी"),
        (spoken_telugu.number_words, 1000, "వెయ్యి"),
        (spoken_telugu.number_words, 2000, "రెండు వేలు"),
        (spoken_telugu.number_words, 21000, "ఇరవై ఒక వేలు"),
        (spoken_telugu.number_words, 300, "మూడు వందలు"),
        (spoken_telugu.number_words, 10**7, "ఒక కోటి"),
        (spoken_telugu.number_words, 3 * 10**7, "మూడు కోట్లు"),
        (spoken_kannada.number_words, 33, "ಮೂವತ್ಮೂರು"),
        (spoken_kannada.number_words, 99, "ತೊಂಬತ್ತೊಂಬತ್ತು"),
        (spoken_kannada.number_words, 900, "ಒಂಬೈನೂರು"),
        (spoken_kannada.number_words, 10**5, "ಒಂದು ಲಕ್ಷ"),
        (spoken_english.number_words, 0, "zero"),
        (spoken_english.number_words, 100000, "one lakh"),
        (spoken_english.number_words, 12345678, f"one crore {TWENTY_THREE_LAKH_ONWARDS}"),
        (spoken_english.number_words, 10**9, "one hundred crore"),
    ],
)
def test_numbers_are_grouped_the_indian_way(
    words: Callable[[int], str], n: int, spoken: str
) -> None:
    assert words(n) == nfc(spoken)


@pytest.mark.parametrize(
    ("words", "year", "spoken"),
    [
        (spoken_marathi.year_words, 1900, "एकोणीसशे"),
        (spoken_marathi.year_words, 2026, "दोन हजार सव्वीस"),
        (spoken_telugu.year_words, 1900, "పందొమ్మిది వందలు"),
        (spoken_telugu.year_words, 1947, "పందొమ్మిది వందల నలభై ఏడు"),
        (spoken_kannada.year_words, 2000, "ಎರಡು ಸಾವಿರ"),
        (spoken_kannada.year_words, 1947, "ಸಾವಿರದ ಒಂಬೈನೂರ ನಲವತ್ತೇಳು"),
        (spoken_english.year_words, 2005, "two thousand five"),
        (spoken_english.year_words, 1905, "nineteen oh five"),
        (spoken_english.year_words, 1900, "nineteen hundred"),
        (spoken_english.year_words, 2026, "twenty twenty six"),
    ],
)
def test_years_are_read_the_way_people_say_them(
    words: Callable[[int], str], year: int, spoken: str
) -> None:
    assert words(year) == nfc(spoken)


@pytest.mark.parametrize(
    ("normalize", "text"),
    [
        (spoken_marathi.normalize, "33:99 वाजता"),
        (spoken_english.normalize, "at 25:00"),
        (spoken_english.normalize, "on 45/13/2026"),
    ],
)
def test_impossible_readings_leave_the_digits_in_place(
    normalize: Callable[[str], str], text: str
) -> None:
    assert any(c.isdigit() for c in normalize(text))


def test_a_marathi_ordinal_suffix_that_does_not_fit_the_number_is_read_as_a_dative() -> None:
    assert spoken_marathi.normalize("उद्या 10ला ये") == nfc("उद्या दहाला ये")


@pytest.mark.parametrize("language", ["en-IN", "hi-IN", "kn-IN", "mr-IN", "te-IN"])
def test_every_focus_language_has_platform_rules(language: str) -> None:
    assert language.split("-")[0] in NORMALIZERS
    assert SpeechPlan(Speech(), language).normalizes


def test_a_switch_of_language_switches_the_rules() -> None:
    plan = SpeechPlan(Speech(), "en-IN")
    assert plan.spoken("Pay ₹500.") == "Pay five hundred rupees."
    plan.speak_in("te-IN")
    assert plan.spoken("₹500 చెల్లించండి.") == nfc("ఐదు వందల రూపాయలు చెల్లించండి.")
