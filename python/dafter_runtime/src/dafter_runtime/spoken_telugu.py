from __future__ import annotations

from .spoken_rules import SpokenLanguage, clock_hour, period_of_day

UNITS = (
    "సున్నా ఒకటి రెండు మూడు నాలుగు ఐదు ఆరు ఏడు ఎనిమిది తొమ్మిది "
    "పది పదకొండు పన్నెండు పదమూడు పద్నాలుగు పదిహేను పదహారు పదిహేడు పద్దెనిమిది పందొమ్మిది"
).split()
TENS = ("", "", "ఇరవై", "ముప్పై", "నలభై", "యాభై", "అరవై", "డెబ్బై", "ఎనభై", "తొంభై")

MONTHS = ("జనవరి ఫిబ్రవరి మార్చి ఏప్రిల్ మే జూన్ జూలై ఆగస్టు సెప్టెంబర్ అక్టోబర్ నవంబర్ డిసెంబర్").split()

SCALES = (
    (10**7, "ఒక కోటి", "కోట్లు", "కోట్ల"),
    (10**5, "ఒక లక్ష", "లక్షలు", "లక్షల"),
    (1000, "వెయ్యి", "వేలు", "వేల"),
)
HUNDRED, HUNDRED_JOINED, HUNDREDS, HUNDREDS_JOINED = "వంద", "నూట", "వందలు", "వందల"
BEFORE_NOUN = {
    "ఒకటి": "ఒక",
    "కోట్లు": "కోట్ల",
    "లక్షలు": "లక్షల",
    "వేలు": "వేల",
    "వందలు": "వందల",
}
HALF_SUFFIX = "న్నర"
POINT = "పాయింట్"
PERIODS = {"morning": "ఉదయం", "afternoon": "మధ్యాహ్నం", "evening": "సాయంత్రం", "night": "రాత్రి"}
ONE_OCLOCK, ONE_OCLOCK_AT = "ఒంటి గంట", "ఒంటి గంటకు"
HOURS, HOURS_AT, MINUTES_AT = "గంటల", "గంటలకు", "నిమిషాలకు"
FIRST = "మొదటి"
ORDINAL_SUFFIX = "వ"
DROPPED_VOWEL_SIGNS = "ుి"
AI_SIGN, AI_ORDINAL = "ై", "య్యవ"


def below_hundred(n: int) -> str:
    if n < 20:
        return UNITS[n]
    tens, units = divmod(n, 10)
    return f"{TENS[tens]} {UNITS[units]}" if units else TENS[tens]


def before_noun(spoken: str) -> str:
    head, _, last = spoken.rpartition(" ")
    last = BEFORE_NOUN.get(last, last)
    return f"{head} {last}" if head else last


def hundreds_words(hundreds: int, more: bool) -> str:
    if hundreds == 1:
        return HUNDRED_JOINED if more else HUNDRED
    return f"{before_noun(UNITS[hundreds])} {HUNDREDS_JOINED if more else HUNDREDS}"


SCALE_WORDS = {
    "వెయ్యి": 1000,
    "వేలు": 1000,
    "వేల": 1000,
    "లక్ష": 10**5,
    "లక్షలు": 10**5,
    "లక్షల": 10**5,
    "కోటి": 10**7,
    "కోట్లు": 10**7,
    "కోట్ల": 10**7,
}
SCALE_NAMES = {1000: "వేల", 10**5: "లక్షల", 10**7: "కోట్ల"}


class SpokenTelugu(SpokenLanguage):
    digits = tuple(UNITS[:10])
    months = tuple(MONTHS)
    scale_words = SCALE_WORDS
    scale_names = SCALE_NAMES
    rupee_words = ("రూపాయలు", "రూపాయల", "రూపాయి")
    rupee_marks = (r"రూ\.?",)
    rupee_one = "రూపాయి"
    rupees_name = "రూపాయలు"
    paise_name = "పైసలు"
    percent_name = "శాతం"
    time_suffixes = ("గంటలకు", "గంటకు", "గంటలు", "గంట")
    ordinal_suffixes = (ORDINAL_SUFFIX,)
    plus = "ప్లస్"

    def number_words(self, n: int) -> str:
        if n < 100:
            return below_hundred(n)
        words: list[str] = []
        for size, one, plural, joined in SCALES:
            count, n = divmod(n, size)
            if count == 1:
                words.append(one)
            elif count:
                words.append(f"{before_noun(self.number_words(count))} {joined if n else plural}")
        hundreds, n = divmod(n, 100)
        if hundreds:
            words.append(hundreds_words(hundreds, n > 0))
        if n:
            words.append(below_hundred(n))
        return " ".join(words)

    def counted(self, n: int) -> str:
        return before_noun(self.number_words(n))

    def year_words(self, year: int) -> str:
        if 1100 <= year <= 1999:
            hundreds, rest = divmod(year, 100)
            spoken = hundreds_words(hundreds, rest > 0)
            return f"{spoken} {below_hundred(rest)}" if rest else spoken
        return self.number_words(year)

    def decimal_words(self, whole: int, fraction: str) -> str:
        if fraction == "5" and 1 <= whole < 100:
            return below_hundred(whole) + HALF_SUFFIX
        return f"{self.number_words(whole)} {POINT} {self.digit_words(fraction)}"

    def time_words(self, hour: int, minute: int, meridiem: str) -> str:
        period = PERIODS.get(period_of_day(hour, meridiem), "")
        h = clock_hour(hour)
        minutes = f"{before_noun(below_hundred(minute))} {MINUTES_AT}"
        if minute == 30:
            spoken = f"{UNITS[h]}{HALF_SUFFIX} {HOURS_AT}"
        elif h == 1:
            spoken = f"{ONE_OCLOCK} {minutes}" if minute else ONE_OCLOCK_AT
        else:
            spoken = f"{UNITS[h]} {HOURS} {minutes}" if minute else f"{UNITS[h]} {HOURS_AT}"
        return f"{period} {spoken}" if period else spoken

    def ordinal_words(self, n: int, suffix: str) -> str | None:
        if n == 1:
            return FIRST
        spoken = self.number_words(n)
        if spoken.endswith(AI_SIGN):
            return spoken[:-1] + AI_ORDINAL
        if spoken[-1] in DROPPED_VOWEL_SIGNS:
            return spoken[:-1] + ORDINAL_SUFFIX
        return spoken + ORDINAL_SUFFIX


SPOKEN = SpokenTelugu()
normalize = SPOKEN.normalize
number_words = SPOKEN.number_words
year_words = SPOKEN.year_words

__all__ = ["SPOKEN", "SpokenTelugu", "normalize", "number_words", "year_words"]
