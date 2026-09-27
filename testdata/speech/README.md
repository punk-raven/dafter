# Spoken Hindi vectors

`hindi-normalization.json` pins what the worker's speech planner hands TTS
for a Hindi reply when `agent.speech.normalization` is `platform`: each case
is a sentence as the LLM writes it (`text`) and the sentence as it is spoken
(`spoken`), grouped by `kind`.

- Numbers use Indian grouping (लाख, करोड़), with or without the commas.
- Rupee amounts, written with ₹, Rs., INR or a trailing रुपये, end in
  रुपये (रुपया for one) and read paise after them; a scale word written
  after the amount (5 लाख, 1.5 करोड़) is kept before रुपये.
- A half is said the way people say it: डेढ़, ढाई, साढ़े.
- Dates are day first (DD/MM/YYYY, DD-MM-YY, ISO YYYY-MM-DD); years
  1100 to 1999 are read as hundreds (उन्नीस सौ सैंतालीस).
- Times use बजे with सवा, साढ़े, पौने for quarter, half and three quarters,
  and name the part of the day for am, pm or a 24 hour clock.
- Phone numbers (8 to 13 digits in groups, or with a country code), codes
  of six or more digits and anything with a leading zero are read digit by
  digit, in groups separated by a pause.
- Numbers glued to Latin letters (COVID-19, 4G, A1) and ratios (2:1) are
  left for TTS.

`python/dafter_runtime/tests/test_speech_plan.py` reads every case. A rule
change that alters how a case is spoken fails there until this file says the
new reading on purpose.
