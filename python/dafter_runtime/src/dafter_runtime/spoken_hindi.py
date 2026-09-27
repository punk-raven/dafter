from __future__ import annotations

import re
import unicodedata
from collections.abc import Callable


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


UNITS = (
    "शून्य एक दो तीन चार पाँच छह सात आठ नौ "
    "दस ग्यारह बारह तेरह चौदह पंद्रह सोलह सत्रह अठारह उन्नीस "
    "बीस इक्कीस बाईस तेईस चौबीस पच्चीस छब्बीस सत्ताईस अट्ठाईस उनतीस "
    "तीस इकतीस बत्तीस तैंतीस चौंतीस पैंतीस छत्तीस सैंतीस अड़तीस उनतालीस "
    "चालीस इकतालीस बयालीस तैंतालीस चवालीस पैंतालीस छियालीस सैंतालीस अड़तालीस उनचास "
    "पचास इक्यावन बावन तिरपन चौवन पचपन छप्पन सत्तावन अट्ठावन उनसठ "
    "साठ इकसठ बासठ तिरसठ चौंसठ पैंसठ छियासठ सड़सठ अड़सठ उनहत्तर "
    "सत्तर इकहत्तर बहत्तर तिहत्तर चौहत्तर पचहत्तर छिहत्तर सतहत्तर अठहत्तर उनासी "
    "अस्सी इक्यासी बयासी तिरासी चौरासी पचासी छियासी सत्तासी अट्ठासी नवासी "
    "नब्बे इक्यानवे बानवे तिरानवे चौरानवे पचानवे छियानवे सत्तानवे अट्ठानवे निन्यानवे"
).split()

MONTHS = ("जनवरी फ़रवरी मार्च अप्रैल मई जून जुलाई अगस्त सितंबर अक्टूबर नवंबर दिसंबर").split()

SCALES = ((10**7, "करोड़"), (10**5, "लाख"), (1000, "हज़ार"), (100, "सौ"))
HALVES = {1: "डेढ़", 2: "ढाई"}
DEVANAGARI_DIGITS = str.maketrans("०१२३४५६७८९", "0123456789")

RUPEE = "रुपये"
ONE_RUPEE = "रुपया"
PAISE = "पैसे"
POINT = "दशमलव"
PERCENT = "प्रतिशत"
PLUS = "प्लस"
PHONE_DIGITS = (8, 13)

NOT_WORD_BEFORE = r"(?<![A-Za-z0-9.,:/+])(?<![A-Za-z]-)"
NOT_WORD_AFTER = r"(?![A-Za-z0-9]|[.,:/]\d)"
AMOUNT = r"\d{1,3}(?:,\d{2})*,\d{3}|\d{1,3}(?:,\d{3})+|\d+"
SCALE_NAMES = {1000: "हज़ार", 10**5: "लाख", 10**7: "करोड़"}
SCALE_WORDS = {
    nfc(word): size
    for word, size in {
        "हज़ार": 1000,
        "हजार": 1000,
        "लाख": 10**5,
        "करोड़": 10**7,
        "करोड": 10**7,
        "thousand": 1000,
        "lakh": 10**5,
        "crore": 10**7,
    }.items()
}


def number_words(n: int) -> str:
    if n < 100:
        return UNITS[n]
    words: list[str] = []
    for size, name in SCALES:
        count, n = divmod(n, size)
        if count:
            words.append(f"{number_words(count)} {name}")
    if n:
        words.append(UNITS[n])
    return " ".join(words)


def digit_words(digits: str) -> str:
    return " ".join(UNITS[int(d)] for d in digits if d.isdigit())


def year_words(year: int) -> str:
    if 1100 <= year <= 1999:
        hundreds, rest = divmod(year, 100)
        return " ".join(w for w in (UNITS[hundreds], "सौ", UNITS[rest] if rest else "") if w)
    return number_words(year)


def decimal_words(whole: int, fraction: str) -> str:
    if fraction == "5" and whole >= 1:
        return HALVES.get(whole, f"साढ़े {number_words(whole)}")
    return f"{number_words(whole)} {POINT} {digit_words(fraction)}"


def quantity_words(text: str) -> str:
    whole, _, fraction = text.replace(",", "").partition(".")
    fraction = fraction.rstrip("0")
    if not fraction:
        return number_words(int(whole))
    return decimal_words(int(whole), fraction)


def rupees(match: re.Match[str]) -> str:
    amount = match.group("amount") or match.group("amount_after")
    scale = match.group("scale") or match.group("scale_after")
    whole, _, paise = amount.replace(",", "").partition(".")
    if scale:
        size = SCALE_WORDS[nfc(scale).lower()]
        return f"{quantity_words(amount)} {SCALE_NAMES[size]} {RUPEE}"
    unit = ONE_RUPEE if int(whole) == 1 else RUPEE
    spoken = f"{number_words(int(whole))} {unit}"
    paise = paise[:2].ljust(2, "0") if paise else ""
    if paise and int(paise):
        spoken += f" {number_words(int(paise))} {PAISE}"
    return spoken


def period_of_day(hour: int, meridiem: str) -> str:
    if meridiem.startswith("a"):
        return "रात" if hour == 12 or hour < 4 else "सुबह"
    if meridiem.startswith("p"):
        if hour == 12 or hour < 4:
            return "दोपहर"
        return "शाम" if hour < 7 else "रात"
    if hour == 0:
        return "रात"
    if hour >= 13:
        return "दोपहर" if hour < 16 else "शाम" if hour < 19 else "रात"
    return ""


def clock_hour(hour: int) -> int:
    hour %= 12
    return 12 if hour == 0 else hour


def time_words(hour: int, minute: int, meridiem: str) -> str:
    period = period_of_day(hour, meridiem)
    h = clock_hour(hour)
    if minute == 0:
        spoken = f"{UNITS[h]} बजे"
    elif minute == 15:
        spoken = f"सवा {UNITS[h]} बजे"
    elif minute == 30:
        spoken = f"{HALVES.get(h, f'साढ़े {UNITS[h]}')} बजे"
    elif minute == 45:
        spoken = f"पौने {UNITS[clock_hour(h + 1)]} बजे"
    else:
        spoken = f"{UNITS[h]} बजकर {UNITS[minute]} मिनट"
    return f"{period} {spoken}" if period else spoken


def times(match: re.Match[str]) -> str:
    hour, minute = int(match.group("hour")), int(match.group("minute"))
    if hour > 23 or minute > 59:
        return match.group(0)
    meridiem = (match.group("meridiem") or "").lower().replace(".", "")
    return time_words(hour, minute, meridiem)


def date_words(day: int, month: int, year: int | None) -> str | None:
    if not 1 <= month <= 12 or not 1 <= day <= 31:
        return None
    spoken = f"{UNITS[day]} {MONTHS[month - 1]}"
    return f"{spoken} {year_words(year)}" if year is not None else spoken


def full_year(text: str) -> int:
    year = int(text)
    return 2000 + year if len(text) == 2 else year


def dates(match: re.Match[str]) -> str:
    if match.group("iso_year"):
        day, month = int(match.group("iso_day")), int(match.group("iso_month"))
        year = int(match.group("iso_year"))
    else:
        day, month = int(match.group("day")), int(match.group("month"))
        year = full_year(match.group("year"))
    return date_words(day, month, year) or match.group(0)


def named_dates(match: re.Match[str]) -> str:
    return f"{number_words(int(match.group('day')))} {match.group('month')} " + year_words(
        int(match.group("year"))
    )


def grouped(digits: str) -> str:
    size = 5 if len(digits) == 10 else 3
    return ", ".join(digit_words(digits[i : i + size]) for i in range(0, len(digits), size))


def phones(match: re.Match[str]) -> str:
    country = match.group("country")
    groups = [g for g in re.split(r"[\s\-]+", match.group("number")) if g]
    digits = sum(len(g) for g in groups)
    if not PHONE_DIGITS[0] <= digits <= PHONE_DIGITS[1] or (country is None and len(groups) < 2):
        return match.group(0)
    spoken = [grouped(g) if len(g) > 5 else digit_words(g) for g in groups]
    if country:
        spoken.insert(0, f"{PLUS} {digit_words(country)}")
    return ", ".join(spoken)


def percents(match: re.Match[str]) -> str:
    return f"{quantity_words(match.group('value'))} {PERCENT}"


def quantities(match: re.Match[str]) -> str:
    return quantity_words(match.group(0))


def long_digits(match: re.Match[str]) -> str:
    return grouped(match.group(0))


def _rule(pattern: str) -> re.Pattern[str]:
    return re.compile(nfc(f"{NOT_WORD_BEFORE}(?:{pattern}){NOT_WORD_AFTER}"), re.IGNORECASE)


MONTH_NAMES = "|".join(MONTHS)
SCALE_ALTERNATIVES = "|".join(SCALE_WORDS)
RULES: tuple[tuple[re.Pattern[str], Callable[[re.Match[str]], str]], ...] = (
    (
        _rule(
            r"(?:(?:₹|rs\.?|inr|रु\.?)\s?(?P<amount>(?:" + AMOUNT + r")(?:\.\d+)?)"
            r"(?:\s(?P<scale>" + SCALE_ALTERNATIVES + r"))?"
            r"|(?P<amount_after>(?:" + AMOUNT + r")(?:\.\d+)?)"
            r"(?:\s(?P<scale_after>" + SCALE_ALTERNATIVES + r"))?"
            r"\s?(?:रुपये|रुपए|रुपया|rupees|rupee))(?:\s?/-)?"
        ),
        rupees,
    ),
    (
        _rule(
            r"(?:(?P<iso_year>\d{4})-(?P<iso_month>\d{1,2})-(?P<iso_day>\d{1,2})"
            r"|(?P<day>\d{1,2})[/.\-](?P<month>\d{1,2})[/.\-](?P<year>\d{4}|\d{2}))"
        ),
        dates,
    ),
    (
        _rule(r"(?P<day>\d{1,2})\s(?P<month>" + MONTH_NAMES + r"),?\s(?P<year>\d{4})"),
        named_dates,
    ),
    (
        _rule(
            r"(?P<hour>\d{1,2}):(?P<minute>\d{2})"
            r"(?:\s?(?P<meridiem>a\.?m\.?|p\.?m\.?))?(?:\s?बजे)?"
        ),
        times,
    ),
    (
        _rule(r"(?:\+(?P<country>\d{1,3})[\s\-]?)?(?P<number>\d{2,10}(?:[\s\-]\d{2,10}){0,3})"),
        phones,
    ),
    (_rule(r"(?P<value>\d+(?:\.\d+)?)\s?%"), percents),
    (_rule(r"0\d+|\d{6,}"), long_digits),
    (_rule(r"\d{1,3}(?:,\d{2})*,\d{3}(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?"), quantities),
    (_rule(r"\d+\.\d+|\d{1,5}"), quantities),
)


def normalize(text: str) -> str:
    text = nfc(text).translate(DEVANAGARI_DIGITS)
    for pattern, speak in RULES:
        text = pattern.sub(speak, text)
    return text


__all__ = [
    "date_words",
    "digit_words",
    "normalize",
    "number_words",
    "time_words",
    "year_words",
]
