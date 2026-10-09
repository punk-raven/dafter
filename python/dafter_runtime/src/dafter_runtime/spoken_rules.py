from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping

DEVANAGARI_ZERO, TELUGU_ZERO, KANNADA_ZERO = 0x0966, 0x0C66, 0x0CE6
INDIC_DIGITS = str.maketrans(
    {
        chr(zero + value): str(value)
        for zero in (DEVANAGARI_ZERO, TELUGU_ZERO, KANNADA_ZERO)
        for value in range(10)
    }
)

NOT_WORD_BEFORE = r"(?<![A-Za-z0-9.,:/+])(?<![A-Za-z]-)"
NOT_WORD_AFTER = r"(?![A-Za-z0-9]|[.,:/]\d)"
WORD_END = r"(?![\wऀ-೿])"
LETTER_BEFORE = r"(?<![\wऀ-೿])"
AMOUNT = r"(?:\d{1,3}(?:,\d{2})*,\d{3}|\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
MERIDIEM = r"[ap]\.m\.|[ap]m"
PHONE_DIGITS = (8, 13)

ENGLISH_MONTHS = (
    "january february march april may june july august september october november december"
).split()
MONTH_ABBREVIATIONS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "sept": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}
ENGLISH_ORDINAL_SUFFIXES = ("st", "nd", "rd", "th")
ENGLISH_SCALE_WORDS = {
    "thousand": 1000,
    "lakh": 10**5,
    "lakhs": 10**5,
    "crore": 10**7,
    "crores": 10**7,
}
CURRENCY_MARKS = ("₹", r"rs\.?", "inr")
ENGLISH_RUPEE_WORDS = ("rupees", "rupee")

Speaker = Callable[[re.Match[str]], str]


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


def words_pattern(words: Iterable[str]) -> str:
    unique = {nfc(w) for w in words}
    return "|".join(re.escape(w) for w in sorted(unique, key=len, reverse=True))


def clock_hour(hour: int) -> int:
    hour %= 12
    return 12 if hour == 0 else hour


def period_of_day(hour: int, meridiem: str) -> str:
    if meridiem.startswith("a"):
        return "night" if hour == 12 or hour < 4 else "morning"
    if meridiem.startswith("p"):
        if hour == 12 or hour < 4:
            return "afternoon"
        return "evening" if hour < 7 else "night"
    if hour == 0:
        return "night"
    if hour >= 13:
        return "afternoon" if hour < 16 else "evening" if hour < 19 else "night"
    return ""


def full_year(text: str) -> int:
    year = int(text)
    return 2000 + year if len(text) == 2 else year


def rule(pattern: str) -> re.Pattern[str]:
    return re.compile(nfc(f"{NOT_WORD_BEFORE}(?:{pattern}){NOT_WORD_AFTER}"), re.IGNORECASE)


class SpokenLanguage(ABC):
    digits: tuple[str, ...]
    months: tuple[str, ...]
    scale_words: Mapping[str, int]
    scale_names: Mapping[int, str]
    rupee_words: tuple[str, ...] = ()
    rupee_marks: tuple[str, ...] = ()
    rupee_one: str
    rupees_name: str
    paise_name: str
    percent_name: str
    percent_first: bool = False
    time_suffixes: tuple[str, ...] = ()
    ordinal_suffixes: tuple[str, ...] = ()
    plus: str

    def __init__(self) -> None:
        english = {name: index + 1 for index, name in enumerate(ENGLISH_MONTHS)}
        native = {nfc(name).lower(): index + 1 for index, name in enumerate(self.months)}
        self.month_numbers = {**MONTH_ABBREVIATIONS, **english, **native}
        scales = {**ENGLISH_SCALE_WORDS, **self.scale_words}
        self.scale_sizes = {nfc(word).lower(): size for word, size in scales.items()}
        self.rules = self.compile_rules()

    @abstractmethod
    def number_words(self, n: int) -> str: ...

    @abstractmethod
    def decimal_words(self, whole: int, fraction: str) -> str: ...

    @abstractmethod
    def time_words(self, hour: int, minute: int, meridiem: str) -> str: ...

    @abstractmethod
    def ordinal_words(self, n: int, suffix: str) -> str | None: ...

    def counted(self, n: int) -> str:
        return self.number_words(n)

    def year_words(self, year: int) -> str:
        return self.number_words(year)

    def day_words(self, day: int) -> str:
        return self.number_words(day)

    def digit_words(self, digits: str) -> str:
        return " ".join(self.digits[int(d)] for d in digits if d.isdigit())

    def quantity_words(self, text: str) -> str:
        whole, _, fraction = text.replace(",", "").partition(".")
        fraction = fraction.rstrip("0")
        if not fraction:
            return self.number_words(int(whole))
        return self.decimal_words(int(whole), fraction)

    def rupees(self, whole: int, paise: int) -> str:
        parts: list[str] = []
        if whole or not paise:
            unit = self.rupee_one if whole == 1 else self.rupees_name
            parts.append(f"{self.counted(whole)} {unit}")
        if paise:
            parts.append(f"{self.counted(paise)} {self.paise_name}")
        return " ".join(parts)

    def scaled_rupees(self, amount: str, size: int) -> str:
        whole, _, fraction = amount.replace(",", "").partition(".")
        fraction = fraction.rstrip("0")
        if not fraction:
            return self.rupees(int(whole) * size, 0)
        quantity = self.decimal_words(int(whole), fraction)
        return f"{quantity} {self.scale_names[size]} {self.rupees_name}"

    def percent_words(self, value: str) -> str:
        quantity = self.quantity_words(value)
        if self.percent_first:
            return f"{self.percent_name} {quantity}"
        return f"{quantity} {self.percent_name}"

    def date_words(self, day: int, month: int, year: int | None) -> str | None:
        if not 1 <= month <= 12 or not 1 <= day <= 31:
            return None
        spoken = f"{self.day_words(day)} {self.months[month - 1]}"
        return f"{spoken} {self.year_words(year)}" if year is not None else spoken

    def grouped(self, digits: str) -> str:
        size = 5 if len(digits) == 10 else 3
        return ", ".join(
            self.digit_words(digits[i : i + size]) for i in range(0, len(digits), size)
        )

    def speak_rupees(self, match: re.Match[str]) -> str:
        amount = match.group("amount") or match.group("amount_after")
        scale = match.group("scale") or match.group("scale_after")
        if scale:
            return self.scaled_rupees(amount, self.scale_sizes[nfc(scale).lower()])
        whole, _, paise = amount.replace(",", "").partition(".")
        written = match.group("rupee_word")
        if written and not paise:
            return f"{self.counted(int(whole))} {written}"
        return self.rupees(int(whole), int(paise[:2].ljust(2, "0")) if paise else 0)

    def speak_dates(self, match: re.Match[str]) -> str:
        if match.group("iso_year"):
            day, month = int(match.group("iso_day")), int(match.group("iso_month"))
            year = int(match.group("iso_year"))
        else:
            day, month = int(match.group("day")), int(match.group("month"))
            year = full_year(match.group("year"))
        return self.date_words(day, month, year) or match.group(0)

    def speak_named_dates(self, match: re.Match[str]) -> str:
        month = self.month_numbers[nfc(match.group("month")).lower()]
        year = match.group("year")
        spoken = self.date_words(int(match.group("day")), month, int(year) if year else None)
        return spoken or match.group(0)

    def speak_times(self, match: re.Match[str]) -> str:
        found = match.groupdict()
        hour, minute = int(found["hour"]), int(found.get("minute") or 0)
        meridiem = (found["meridiem"] or "").lower().replace(".", "")
        if hour > 23 or minute > 59 or (meridiem and not 1 <= hour <= 12):
            return match.group(0)
        return self.time_words(hour, minute, meridiem)

    def speak_phones(self, match: re.Match[str]) -> str:
        country = match.group("country")
        groups = [g for g in re.split(r"[\s\-]+", match.group("number")) if g]
        digits = sum(len(g) for g in groups)
        lowest, highest = PHONE_DIGITS
        if not lowest <= digits <= highest or (country is None and len(groups) < 2):
            return match.group(0)
        spoken = [self.grouped(g) if len(g) > 5 else self.digit_words(g) for g in groups]
        if country:
            spoken.insert(0, f"{self.plus} {self.digit_words(country)}")
        return ", ".join(spoken)

    def speak_percents(self, match: re.Match[str]) -> str:
        return self.percent_words(match.group("value"))

    def speak_ordinals(self, match: re.Match[str]) -> str:
        n, suffix = int(match.group("number")), nfc(match.group("suffix")).lower()
        if n == 0:
            return match.group(0)
        return self.ordinal_words(n, suffix) or match.group(0)

    def speak_long_digits(self, match: re.Match[str]) -> str:
        return self.grouped(match.group(0))

    def speak_quantities(self, match: re.Match[str]) -> str:
        return self.quantity_words(match.group(0))

    def compile_rules(self) -> tuple[tuple[re.Pattern[str], Speaker], ...]:
        scales = words_pattern(self.scale_sizes)
        rupee_words = words_pattern((*self.rupee_words, *ENGLISH_RUPEE_WORDS))
        marks = "|".join((*CURRENCY_MARKS, *self.rupee_marks))
        months = words_pattern(self.month_numbers)
        ordinals = words_pattern((*ENGLISH_ORDINAL_SUFFIXES, *self.ordinal_suffixes))
        suffix = ""
        if self.time_suffixes:
            suffix = rf"(?:\s?(?:{words_pattern(self.time_suffixes)}){WORD_END})?"
        day = r"(?P<day>\d{1,2})(?:st|nd|rd|th)?"
        year = r"(?:,?\s(?P<year>\d{4}))?"
        return (
            (
                rule(
                    rf"(?:(?:{marks})\s?(?P<amount>{AMOUNT})"
                    rf"(?:\s(?P<scale>{scales}){WORD_END})?"
                    rf"|(?P<amount_after>{AMOUNT})(?:\s(?P<scale_after>{scales}))?"
                    rf"\s?(?P<rupee_word>{rupee_words}){WORD_END})(?:\s?/-)?"
                ),
                self.speak_rupees,
            ),
            (
                rule(
                    r"(?:(?P<iso_year>\d{4})-(?P<iso_month>\d{1,2})-(?P<iso_day>\d{1,2})"
                    r"|(?P<day>\d{1,2})[/.\-](?P<month>\d{1,2})[/.\-](?P<year>\d{4}|\d{2}))"
                ),
                self.speak_dates,
            ),
            (
                rule(rf"{day}\s(?:of\s)?(?P<month>{months}){WORD_END}{year}"),
                self.speak_named_dates,
            ),
            (
                rule(rf"{LETTER_BEFORE}(?P<month>{months})\s{day}{year}"),
                self.speak_named_dates,
            ),
            (
                rule(
                    rf"(?P<hour>\d{{1,2}}):(?P<minute>\d{{2}})"
                    rf"(?:\s?(?P<meridiem>{MERIDIEM}))?{suffix}"
                ),
                self.speak_times,
            ),
            (rule(rf"(?P<hour>\d{{1,2}})\s?(?P<meridiem>{MERIDIEM}){suffix}"), self.speak_times),
            (
                rule(
                    r"(?:\+(?P<country>\d{1,3})[\s\-]?)?"
                    r"(?P<number>\d{2,10}(?:[\s\-]\d{2,10}){0,3})"
                ),
                self.speak_phones,
            ),
            (rule(r"(?P<value>\d+(?:\.\d+)?)\s?%"), self.speak_percents),
            (
                rule(rf"(?P<number>\d{{1,7}})(?P<suffix>{ordinals}){WORD_END}"),
                self.speak_ordinals,
            ),
            (rule(r"0\d+|\d{6,}"), self.speak_long_digits),
            (
                rule(r"\d{1,3}(?:,\d{2})*,\d{3}(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?"),
                self.speak_quantities,
            ),
            (rule(r"\d+\.\d+|\d{1,5}"), self.speak_quantities),
        )

    def normalize(self, text: str) -> str:
        text = nfc(text).translate(INDIC_DIGITS)
        for pattern, speak in self.rules:
            text = pattern.sub(speak, text)
        return nfc(text)


__all__ = [
    "ENGLISH_ORDINAL_SUFFIXES",
    "SpokenLanguage",
    "clock_hour",
    "nfc",
    "period_of_day",
]
