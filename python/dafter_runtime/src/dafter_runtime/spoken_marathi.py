from __future__ import annotations

from .spoken_rules import ENGLISH_ORDINAL_SUFFIXES, SpokenLanguage, clock_hour, period_of_day

UNITS = (
    "शून्य एक दोन तीन चार पाच सहा सात आठ नऊ "
    "दहा अकरा बारा तेरा चौदा पंधरा सोळा सतरा अठरा एकोणीस "
    "वीस एकवीस बावीस तेवीस चोवीस पंचवीस सव्वीस सत्तावीस अठ्ठावीस एकोणतीस "
    "तीस एकतीस बत्तीस तेहतीस चौतीस पस्तीस छत्तीस सदतीस अडतीस एकोणचाळीस "
    "चाळीस एक्केचाळीस बेचाळीस त्रेचाळीस चव्वेचाळीस पंचेचाळीस सेहेचाळीस सत्तेचाळीस "
    "अठ्ठेचाळीस एकोणपन्नास "
    "पन्नास एक्कावन्न बावन्न त्रेपन्न चोपन्न पंचावन्न छप्पन्न सत्तावन्न अठ्ठावन्न एकोणसाठ "
    "साठ एकसष्ट बासष्ट त्रेसष्ट चौसष्ट पासष्ट सहासष्ट सदुसष्ट अडुसष्ट एकोणसत्तर "
    "सत्तर एक्काहत्तर बाहत्तर त्र्याहत्तर चौऱ्याहत्तर पंच्याहत्तर शहात्तर सत्याहत्तर "
    "अठ्ठ्याहत्तर एकोणऐंशी "
    "ऐंशी एक्क्याऐंशी ब्याऐंशी त्र्याऐंशी चौऱ्याऐंशी पंच्याऐंशी शहाऐंशी सत्त्याऐंशी "
    "अठ्ठ्याऐंशी एकोणनव्वद "
    "नव्वद एक्क्याण्णव ब्याण्णव त्र्याण्णव चौऱ्याण्णव पंच्याण्णव शहाण्णव सत्त्याण्णव "
    "अठ्ठ्याण्णव नव्व्याण्णव"
).split()

MONTHS = ("जानेवारी फेब्रुवारी मार्च एप्रिल मे जून जुलै ऑगस्ट सप्टेंबर ऑक्टोबर नोव्हेंबर डिसेंबर").split()

SCALES = ((10**7, "कोटी"), (10**5, "लाख"), (1000, "हजार"))
HUNDRED = "शंभर"
HUNDREDS_SUFFIX = "शे"
HALVES = {1: "दीड", 2: "अडीच"}
HALF_MORE = "साडे"
POINT = "पॉइंट"
PERIODS = {"morning": "सकाळी", "afternoon": "दुपारी", "evening": "संध्याकाळी", "night": "रात्री"}
AT_HOUR = "वाजता"

ORDINAL_STEMS = {
    1: "पहिल",
    2: "दुसर",
    3: "तिसर",
    4: "चौथ",
    5: "पाचव",
    6: "सहाव",
    7: "सातव",
    8: "आठव",
    9: "नवव",
    10: "दहाव",
}
ORDINAL_SUFFIX_NUMBERS = {"ल": (1,), "र": (2, 3), "थ": (4,)}
FIRST_FREE_ORDINAL = 5
MASCULINE_ENDING = "ा"
VOWEL_SIGN_ENDINGS = "ाीे"


def below_hundred_half(whole: int) -> str:
    return HALVES.get(whole, f"{HALF_MORE} {UNITS[whole]}")


def ordinal_stem(spoken: str) -> str:
    if spoken.endswith("ीस"):
        return f"{spoken[:-2]}िसाव"
    if spoken[-1] in VOWEL_SIGN_ENDINGS:
        return f"{spoken}व"
    return f"{spoken}ाव"


SCALE_WORDS = {"हजार": 1000, "हज़ार": 1000, "लाख": 10**5, "कोटी": 10**7}
SCALE_NAMES = {1000: "हजार", 10**5: "लाख", 10**7: "कोटी"}


class SpokenMarathi(SpokenLanguage):
    digits = tuple(UNITS[:10])
    months = tuple(MONTHS)
    scale_words = SCALE_WORDS
    scale_names = SCALE_NAMES
    rupee_words = ("रुपये", "रुपया", "रुपए")
    rupee_marks = (r"रु\.?",)
    rupee_one = "रुपया"
    rupees_name = "रुपये"
    paise_name = "पैसे"
    percent_name = "टक्के"
    time_suffixes = ("वाजता", "वाजले")
    ordinal_suffixes = ("ला", "ली", "ले", "रा", "री", "रे", "था", "थी", "थे", "वा", "वी", "वे")
    plus = "प्लस"

    def number_words(self, n: int) -> str:
        if n < 100:
            return UNITS[n]
        words: list[str] = []
        for size, name in SCALES:
            count, n = divmod(n, size)
            if count:
                words.append(f"{self.number_words(count)} {name}")
        hundreds, n = divmod(n, 100)
        if hundreds:
            words.append(HUNDRED if hundreds == 1 and not n else UNITS[hundreds] + HUNDREDS_SUFFIX)
        if n:
            words.append(UNITS[n])
        return " ".join(words)

    def year_words(self, year: int) -> str:
        if 1100 <= year <= 1999:
            hundreds, rest = divmod(year, 100)
            spoken = UNITS[hundreds] + HUNDREDS_SUFFIX
            return f"{spoken} {UNITS[rest]}" if rest else spoken
        return self.number_words(year)

    def decimal_words(self, whole: int, fraction: str) -> str:
        if fraction == "5" and 1 <= whole < 100:
            return below_hundred_half(whole)
        return f"{self.number_words(whole)} {POINT} {self.digit_words(fraction)}"

    def time_words(self, hour: int, minute: int, meridiem: str) -> str:
        period = PERIODS.get(period_of_day(hour, meridiem), "")
        h = clock_hour(hour)
        if minute == 0:
            spoken = f"{UNITS[h]} {AT_HOUR}"
        elif minute == 15:
            spoken = f"सव्वा {UNITS[h]} {AT_HOUR}"
        elif minute == 30:
            spoken = f"{below_hundred_half(h)} {AT_HOUR}"
        elif minute == 45:
            spoken = f"पावणे {UNITS[clock_hour(h + 1)]} {AT_HOUR}"
        else:
            spoken = f"{UNITS[h]} वाजून {UNITS[minute]} मिनिटांनी"
        return f"{period} {spoken}" if period else spoken

    def ordinal_words(self, n: int, suffix: str) -> str | None:
        ending = MASCULINE_ENDING
        if suffix not in ENGLISH_ORDINAL_SUFFIXES:
            allowed = ORDINAL_SUFFIX_NUMBERS.get(suffix[0])
            fits = n in allowed if allowed is not None else n >= FIRST_FREE_ORDINAL
            if not fits:
                return None
            ending = suffix[-1]
        stem = ORDINAL_STEMS.get(n) or ordinal_stem(self.number_words(n))
        return stem + ending


SPOKEN = SpokenMarathi()
normalize = SPOKEN.normalize
number_words = SPOKEN.number_words
year_words = SPOKEN.year_words

__all__ = ["SPOKEN", "SpokenMarathi", "normalize", "number_words", "year_words"]
