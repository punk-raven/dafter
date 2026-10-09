from __future__ import annotations

from .spoken_rules import SpokenLanguage, clock_hour, period_of_day

UNITS = (
    "ಸೊನ್ನೆ ಒಂದು ಎರಡು ಮೂರು ನಾಲ್ಕು ಐದು ಆರು ಏಳು ಎಂಟು ಒಂಬತ್ತು "
    "ಹತ್ತು ಹನ್ನೊಂದು ಹನ್ನೆರಡು ಹದಿಮೂರು ಹದಿನಾಲ್ಕು ಹದಿನೈದು ಹದಿನಾರು ಹದಿನೇಳು ಹದಿನೆಂಟು ಹತ್ತೊಂಬತ್ತು"
).split()
TENS = ("", "", "ಇಪ್ಪತ್ತು", "ಮೂವತ್ತು", "ನಲವತ್ತು", "ಐವತ್ತು", "ಅರವತ್ತು", "ಎಪ್ಪತ್ತು", "ಎಂಬತ್ತು", "ತೊಂಬತ್ತು")
UNITS_AFTER_TENS = {
    1: (1, "ೊಂದು"),
    2: (1, "ೆರಡು"),
    3: (2, "ಮೂರು"),
    4: (2, "ನಾಲ್ಕು"),
    5: (1, "ೈದು"),
    6: (1, "ಾರು"),
    7: (1, "ೇಳು"),
    8: (1, "ೆಂಟು"),
    9: (1, "ೊಂಬತ್ತು"),
}
HUNDREDS = (
    "",
    "ನೂರು",
    "ಇನ್ನೂರು",
    "ಮುನ್ನೂರು",
    "ನಾನೂರು",
    "ಐನೂರು",
    "ಆರುನೂರು",
    "ಏಳುನೂರು",
    "ಎಂಟುನೂರು",
    "ಒಂಬೈನೂರು",
)

MONTHS = ("ಜನವರಿ ಫೆಬ್ರವರಿ ಮಾರ್ಚ್ ಏಪ್ರಿಲ್ ಮೇ ಜೂನ್ ಜುಲೈ ಆಗಸ್ಟ್ ಸೆಪ್ಟೆಂಬರ್ ಅಕ್ಟೋಬರ್ ನವೆಂಬರ್ ಡಿಸೆಂಬರ್").split()

SCALES = ((10**7, "ಕೋಟಿ"), (10**5, "ಲಕ್ಷ"), (1000, "ಸಾವಿರ"))
FINAL_U = "ು"
HALF_ENDING = "ೂವರೆ"
POINT = "ಪಾಯಿಂಟ್"
PERIODS = {"morning": "ಬೆಳಿಗ್ಗೆ", "afternoon": "ಮಧ್ಯಾಹ್ನ", "evening": "ಸಂಜೆ", "night": "ರಾತ್ರಿ"}
HOUR, HOUR_AT, MINUTES_AT = "ಗಂಟೆ", "ಗಂಟೆಗೆ", "ನಿಮಿಷಕ್ಕೆ"
YEAR_THOUSAND = "ಸಾವಿರದ"
FIRST = "ಮೊದಲನೇ"
ORDINAL_SUFFIX = "ನೇ"


def below_hundred(n: int) -> str:
    if n < 20:
        return UNITS[n]
    tens, units = divmod(n, 10)
    if not units:
        return TENS[tens]
    cut, tail = UNITS_AFTER_TENS[units]
    return TENS[tens][:-cut] + tail


def and_a_half(spoken: str) -> str | None:
    return spoken[:-1] + HALF_ENDING if spoken.endswith(FINAL_U) else None


SCALE_WORDS = {"ಸಾವಿರ": 1000, "ಲಕ್ಷ": 10**5, "ಕೋಟಿ": 10**7}
SCALE_NAMES = {1000: "ಸಾವಿರ", 10**5: "ಲಕ್ಷ", 10**7: "ಕೋಟಿ"}


class SpokenKannada(SpokenLanguage):
    digits = tuple(UNITS[:10])
    months = tuple(MONTHS)
    scale_words = SCALE_WORDS
    scale_names = SCALE_NAMES
    rupee_words = ("ರೂಪಾಯಿಗಳು", "ರೂಪಾಯಿ")
    rupee_marks = (r"ರೂ\.?",)
    rupee_one = "ರೂಪಾಯಿ"
    rupees_name = "ರೂಪಾಯಿ"
    paise_name = "ಪೈಸೆ"
    percent_name = "ಶೇಕಡಾ"
    percent_first = True
    time_suffixes = ("ಗಂಟೆಗೆ", "ಗಂಟೆ")
    ordinal_suffixes = (ORDINAL_SUFFIX,)
    plus = "ಪ್ಲಸ್"

    def number_words(self, n: int) -> str:
        if n < 100:
            return below_hundred(n)
        words: list[str] = []
        for size, name in SCALES:
            count, n = divmod(n, size)
            if count:
                words.append(f"{self.number_words(count)} {name}")
        hundreds, n = divmod(n, 100)
        if hundreds:
            words.append(HUNDREDS[hundreds][:-1] if n else HUNDREDS[hundreds])
        if n:
            words.append(below_hundred(n))
        return " ".join(words)

    def decimal_words(self, whole: int, fraction: str) -> str:
        half = and_a_half(below_hundred(whole)) if fraction == "5" and 1 <= whole < 100 else None
        return half or f"{self.number_words(whole)} {POINT} {self.digit_words(fraction)}"

    def time_words(self, hour: int, minute: int, meridiem: str) -> str:
        period = PERIODS.get(period_of_day(hour, meridiem), "")
        h = clock_hour(hour)
        half = and_a_half(UNITS[h])
        if minute == 0:
            spoken = f"{UNITS[h]} {HOUR_AT}"
        elif minute == 30 and half:
            spoken = f"{half} {HOUR_AT}"
        else:
            spoken = f"{UNITS[h]} {HOUR} {below_hundred(minute)} {MINUTES_AT}"
        return f"{period} {spoken}" if period else spoken

    def year_words(self, year: int) -> str:
        thousands, rest = divmod(year, 1000)
        if not 1 <= thousands <= 2 or not rest:
            return self.number_words(year)
        prefix = YEAR_THOUSAND if thousands == 1 else f"{UNITS[thousands]} {YEAR_THOUSAND}"
        return f"{prefix} {self.number_words(rest)}"

    def ordinal_words(self, n: int, suffix: str) -> str | None:
        if n == 1:
            return FIRST
        spoken = self.number_words(n)
        stem = spoken[:-1] if spoken.endswith(FINAL_U) else spoken
        return stem + ORDINAL_SUFFIX


SPOKEN = SpokenKannada()
normalize = SPOKEN.normalize
number_words = SPOKEN.number_words
year_words = SPOKEN.year_words

__all__ = ["SPOKEN", "SpokenKannada", "normalize", "number_words", "year_words"]
