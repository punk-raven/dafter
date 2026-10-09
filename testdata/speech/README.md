# Spoken-form vectors

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

`marathi-`, `telugu-`, `kannada-` and `english-normalization.json` pin the
same for `spoken_marathi.py`, `spoken_telugu.py`, `spoken_kannada.py` and
`spoken_english.py` (shared rules in `spoken_rules.py`), with the same
grouping and digit-by-digit rules plus `ordinal` and `indic-digits` kinds
(Devanagari, Telugu and Kannada digits read as numbers). Each is
`{"reviewed": false, "cases": [...]}`: `reviewed` turns true only once a
native speaker has checked every reading, and none has been yet. The Hindi
file stays a plain list of cases.

`python/dafter_runtime/tests/test_speech_plan.py` reads every Hindi case and
`test_spoken_languages.py` every case of the other four. A rule change that
alters how a case is spoken fails there until the file says the new reading
on purpose.

## TTS round trip

`uv run dafter-tts` (from `python/`) checks that the TTS a job vector names
says these spoken forms intelligibly over a phone line. `run` spends TTS
and STT credits; `sheet` and `tally` call no provider.

    uv run dafter-tts run --job ../testdata/agent/hindi-webrtc-job.json --out hi-a.json

- `run` synthesizes each case's `spoken` with `--voice` (default `priya`),
  resamples it to 8 kHz through G.711 mu-law, transcribes it with the job's
  STT and scores CER against `spoken`, per kind and overall, plus entity
  accuracy (whether each number, amount, phone or date was heard as spoken
  or as written).
- `--language` defaults to the job's; `--limit N` takes the first N cases.
- Audio is written to `--audio-dir/<language>/<case>.wav`, by default the
  git-ignored `testdata/speech/audio/`. `sheet` copies it from there.
- `--max-inr` (default 25) caps the spend: the run stops before any provider
  call if the priced synthesis plus an estimated ASR pass is over it, and
  again before transcribing once the real audio length is known. A provider
  missing from the price table stops it too.

### Baselines

`tts-baselines.json` (`--baseline` to use another file) holds one CER per
language, TTS provider, model, voice, STT provider and model, plus a shared
`tolerance`. `run` reports `held`, `regressed` or `unrecorded` and exits 1
on `regressed` (CER above baseline plus tolerance). `--record-baseline`
writes this run's CER as the new baseline for its key; record only from a
run that is meant to be the reference.

### Blind A/B listening

1. Run `run --out` twice for the same language, changing the TTS or voice.
2. `uv run dafter-tts sheet a.json b.json --sheet-dir sheet/ --key key.json`
   writes `sheet/sheet.csv` and `sheet/audio/pNNN-1.wav`, `pNNN-2.wav` in a
   shuffled order per pair (`--seed` to repeat it). Raters get only
   `sheet/`; keep `key.json` from them.
3. Each rater fills `preferred` (`first`, `second`, `same` or blank),
   `first_naturalness` and `second_naturalness` (1 to 5) and `rater`.
4. `uv run dafter-tts tally filled-*.csv --key key.json --out ratings.json`
   unblinds and averages them per variant.
5. `uv run dafter-scorecard --tts a.json b.json --naturalness ratings.json`
   shows CER, entity accuracy and naturalness side by side.
