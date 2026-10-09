# Error rate vectors

`uv run dafter-wer` (from `python/`, `python/dafter_evals/src/dafter_evals/score.py`)
scores a transcript against the verified reference of a known clip:

    uv run dafter-wer --reference ../testdata/wer/<clip>.json \
      --transcript export.json --rendering verbatim --participant p_4b81e0d7

`--transcript` takes a transcript version as `GET
/sessions/{id}/transcripts/{version}` (worker credential) exports it; `--text` takes plain text
instead. It prints the clip, the rendering, `wer` (substitutions, deletions and
insertions over the reference's word count) and each count, `cer` with
`referenceCharacters` and `characterErrors`, and `oiwer` with
`oiwerReferenceWords` and `oiwerErrors`. It calls no provider.

## Reference format

One JSON file per clip, `<clip>.json`, committed:

- `clip`: the clip's name, which is also its audio file's stem
- `language`: the BCP 47 tag the session ran under
- `audio`: where the audio lives, `testdata/wer/audio/<clip>.<ext>`
- `speakers`: how many people speak in it
- `reference`: what was said, verified by a person listening to the clip,
  written the way the verbatim rendering writes it (words as spoken, numbers
  as spoken)
- `alternates` (optional): other valid spellings of the same speech, such as
  a romanized reference beside the native-script one; only OIWER reads them

## Where the clip goes

The audio is never committed: it is a real person's voice, collected with
their consent, and `testdata/wer/audio/` is git-ignored. Put the known Hindi
clip at `testdata/wer/audio/<clip>.ogg` (or `.wav`) and its reference beside
this README. To score the pipeline on it, play it into a session created
with `transcription.mode` `after_call` or `both` and a track recording of the
speaker, run `uv run dafter-batch <session>` after the call, export the
version and run the command above with that speaker's participant id.

## Normalisation

Both sides are normalised the same way before words are compared, so a
transcript is not charged for spelling a recognizer is free to choose:
Unicode NFC; the nukta dropped (ज़ and ज, क़ and क are one letter here,
because recognizers are inconsistent about it); chandrabindu read as
anusvara (हाँ and हां); zero-width joiners removed; Devanagari digits read
as ASCII digits; the same three for Kannada and Telugu (their nukta dropped,
candrabindu read as anusvara, digits read as ASCII digits); punctuation and symbols, the danda and double danda
included, turned into spaces; Latin case folded. Numbers are not converted
between words and digits: दस and 10 are different words, which is why the
reference is compared with the verbatim rendering.

`example-reference.json` and `example-transcript.json` illustrate both
formats; no audio stands behind them, and
`python/dafter_evals/tests/test_wer.py` scores one against the other.

## Metrics

All three are computed on the normalised text above, by `wer.py`.

- **WER**: word substitutions, deletions and insertions over the reference's
  word count, against `reference` only.
- **CER**: the same edits over Unicode code points (after NFC and the rules
  above) of the reference with every space removed, so a word split or joined
  differently costs nothing and a wrong vowel sign costs one character, not
  one word. Code points, not grapheme clusters: क्ष is three.
- **OIWER** (orthography-aware WER): `reference` and each alternate are
  valid spellings. References with the same word count are aligned word by
  word, so each position accepts any of their words (मेरा photo भेजो scores 0
  against मेरा फोटो भेजो and mera photo bhejo); a reference with another word
  count is scored on its own. The lowest rate wins, so OIWER never exceeds
  WER.
- **Entity accuracy** (`dafter-asr`, golden set): each labelled span (number,
  phone, amount, name, address, date) is heard when its normalised words
  appear contiguously and exactly in the normalised transcript. Spaces
  between digits are dropped on both sides first, so 98765 43210 and
  9876543210 match; 50 inside 500 does not.

## Gates

`dafter-asr run` reports every metric and names one primary metric per
language: WER for en, hi and mr, CER for te and kn (word boundaries in
agglutinative Telugu and Kannada make WER swing on spacing alone). A run is
compared with its own baseline in `testdata/asr/baselines.json`, keyed by
dataset, language, provider, model, mode and hearing: it regresses when the
primary metric rises more than `tolerance` (an absolute 0.02) above the
recorded value. A flat threshold is not used because Indic baselines differ
by language and set. `--record-baseline` stores the run's value; a run with
no baseline is reported as `unrecorded` and does not fail.
