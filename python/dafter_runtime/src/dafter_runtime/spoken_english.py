from __future__ import annotations

from .spoken_rules import SpokenLanguage, clock_hour, period_of_day

UNITS = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen "
    "fifteen sixteen seventeen eighteen nineteen"
).split()
TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety")
MONTHS = (
    "January February March April May June July August September October November December"
).split()

SCALES = ((10**7, "crore"), (10**5, "lakh"), (1000, "thousand"), (100, "hundred"))
IRREGULAR_ORDINALS = {
    "one": "first",
    "two": "second",
    "three": "third",
    "five": "fifth",
    "eight": "eighth",
    "nine": "ninth",
    "twelve": "twelfth",
}
PERIODS = {
    "morning": "in the morning",
    "afternoon": "in the afternoon",
    "evening": "in the evening",
    "night": "at night",
}
NOON, MIDNIGHT = "twelve noon", "twelve midnight"


def below_hundred(n: int) -> str:
    if n < 20:
        return UNITS[n]
    tens, units = divmod(n, 10)
    return f"{TENS[tens]} {UNITS[units]}" if units else TENS[tens]


def ordinal_of(spoken: str) -> str:
    head, _, last = spoken.rpartition(" ")
    if last in IRREGULAR_ORDINALS:
        last = IRREGULAR_ORDINALS[last]
    elif last.endswith("y"):
        last = f"{last[:-1]}ieth"
    else:
        last = f"{last}th"
    return f"{head} {last}" if head else last


SCALE_WORDS: dict[str, int] = {}
SCALE_NAMES = {1000: "thousand", 10**5: "lakh", 10**7: "crore"}


class SpokenEnglish(SpokenLanguage):
    digits = tuple(UNITS[:10])
    months = tuple(MONTHS)
    scale_words = SCALE_WORDS
    scale_names = SCALE_NAMES
    rupee_one = "rupee"
    rupees_name = "rupees"
    paise_name = "paise"
    percent_name = "percent"
    time_suffixes = ("hrs",)
    plus = "plus"

    def number_words(self, n: int) -> str:
        if n < 100:
            return below_hundred(n)
        words: list[str] = []
        for size, name in SCALES:
            count, n = divmod(n, size)
            if count:
                words.append(f"{self.number_words(count)} {name}")
        if n:
            words.append(below_hundred(n))
        return " ".join(words)

    def year_words(self, year: int) -> str:
        if not 1100 <= year <= 2099 or 2000 <= year <= 2009:
            return self.number_words(year)
        century, rest = divmod(year, 100)
        if rest == 0:
            return f"{below_hundred(century)} hundred"
        if rest < 10:
            return f"{below_hundred(century)} oh {UNITS[rest]}"
        return f"{below_hundred(century)} {below_hundred(rest)}"

    def day_words(self, day: int) -> str:
        return ordinal_of(self.number_words(day))

    def decimal_words(self, whole: int, fraction: str) -> str:
        if fraction == "5" and 1 <= whole < 100:
            return f"{below_hundred(whole)} and a half"
        return f"{self.number_words(whole)} point {self.digit_words(fraction)}"

    def time_words(self, hour: int, minute: int, meridiem: str) -> str:
        period = period_of_day(hour, meridiem)
        h = clock_hour(hour)
        if minute == 0 and h == 12 and period:
            return NOON if period == "afternoon" else MIDNIGHT
        spoken = UNITS[h]
        if minute:
            spoken += f" oh {UNITS[minute]}" if minute < 10 else f" {below_hundred(minute)}"
        if period:
            return f"{spoken} {PERIODS[period]}"
        return spoken if minute else f"{spoken} o'clock"

    def ordinal_words(self, n: int, suffix: str) -> str | None:
        return ordinal_of(self.number_words(n))


SPOKEN = SpokenEnglish()
normalize = SPOKEN.normalize
number_words = SPOKEN.number_words
year_words = SPOKEN.year_words

__all__ = ["SPOKEN", "SpokenEnglish", "normalize", "number_words", "year_words"]
