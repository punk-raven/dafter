# Public speech samples for word error rate per language

`uv run dafter-asr` (from `python/`, `python/dafter_evals/src/dafter_evals/asr.py`)
measures the word error rate of the STT the worker builds, language by
language, on public recordings, before any live call. The samples are small
and pinned; the audio is never committed.

## What is pinned

`sources.json` pins each source; `python/dafter_evals/src/dafter_evals/loaders.py`
maps each one to the loader that pins and fetches it.

- **Kathbath** (kn-IN, hi, mr-IN, te-IN), read speech from AI4Bharat
  (https://arxiv.org/abs/2208.11761), CC-BY-4.0, as the Vistaar benchmark
  packages its test sets (https://github.com/AI4Bharat/vistaar): one zip,
  pinned by its URL, byte size and ETag. `dafter-asr` reads only the members it
  needs from it by HTTP range, so pinning a language downloads about 6 MB, not
  the 3.5 GB archive, and a changed archive is refused.
- **Svarah** (en-IN), Indian-accented English from AI4Bharat
  (https://arxiv.org/abs/2305.15760), CC-BY-4.0.
- **IndicVoices** (hi, kn-IN, mr-IN, te-IN), spontaneous and read speech from
  AI4Bharat (https://arxiv.org/abs/2403.01926), CC-BY-4.0, its `valid` split,
  one dataset config per language (`hindi`, `kannada`, `marathi`, `telugu`).
- **Lahaja** (hi), Hindi from speakers of many accents
  (https://arxiv.org/abs/2408.11440), MIT, its `test` split.
- **MUCS 2021 sub-task 1** (`mucs2021`: hi, mr-IN), read speech recorded over
  the phone at 8 kHz (https://www.openslr.org/103/), each language's test
  tarball pinned by its URL, byte size and ETag.
- **MUCS 2021 sub-task 2** (`mucs2021-codeswitch`: hi), Hindi-English
  code-switched lectures at 16 kHz (https://www.openslr.org/104/),
  CC-BY-SA-4.0, the test tarball pinned the same way.

Svarah, IndicVoices and Lahaja live on Hugging Face, pinned by revision; a
dataset at any other revision is refused. All three are gated: pinning or
fetching them needs `HF_TOKEN` from an account that has accepted each
dataset's terms on its page (https://huggingface.co/datasets/ai4bharat/Svarah,
`/IndicVoices`, `/Lahaja`). Rows and audio come from the Hugging Face datasets
server, which rate-limits a pin partway through; `dafter-asr` retries a 429 or
5xx with backoff and stops at once on any other refusal. Rows with an empty
reference are skipped.

A gzipped tarball cannot be read by range, so a MUCS pin or fetch downloads
the whole test tarball (235 to 444 MB) once into
`audio/archives/<dataset>-<language>.tar.gz`, refusing it when its size or
ETag differ from the pin, and reads every clip from that copy. Sub-task 1
clips are whole files named by `transcription.txt`; sub-task 2 clips are cut
from each lecture at its Kaldi `segments` bounds, so a sub-task 2 member is
`<lecture>.wav#<start>-<end>` and the clip is that cut, re-encoded as WAV.
Audio at any rate other than the pinned `sampleRateHz` is refused. The
archives' internal layout was not inspected when the loader was written: the
first pin of each confirms it.

`<dataset>-<language>.json` is one pinned sample: the source it came from,
the selection rule, and per clip its id, its member in the source, its
speaker, duration, sha256 and the dataset's reference transcript. The rule:
rows 3 to 12 s long, in path or row order, one clip per speaker, taken in
turn from each gender, 20 clips. Each committed Kathbath sample is 20
speakers, 10 of each gender, 136 to 178 s of audio. Svarah names no speaker,
so its sample takes one clip per first language and district instead: 20 of
them, 124 s of audio. MUCS names no gender: its speaker is `utt2spk` when the
archive has one, else the segment's lecture, else the utterance id up to its
first underscore. A per-language `speakers` in `sources.json` caps the
sample below 20 clips: the MUCS Hindi test set has 19 speakers. A MUCS
manifest also carries its tarball's URL, byte size and ETag.

## Commands

    uv run dafter-asr pin indicvoices --language te-IN
    uv run dafter-asr fetch ../testdata/asr/kathbath-kn-IN.json
    uv run dafter-asr run ../testdata/asr/kathbath-kn-IN.json \
      --job ../testdata/agent/kannada-webrtc-job.json --out kn.json
    uv run dafter-asr run --set golden --language hi \
      --job ../testdata/agent/hindi-telephony-job.json

`pin` chooses a sample from a source (`kathbath`, `svarah`, `indicvoices`,
`lahaja`, `mucs2021`, `mucs2021-codeswitch`) by the rule above, writes its
manifest beside this README and stores its clips. `fetch` downloads a
sample's clips into `testdata/asr/audio/` (git-ignored) and refuses any whose
sha256 differs. `run` streams each clip in real time,
followed by 1.5 s of silence, through the STT the job vector names, built by
the providers' registry exactly as the worker builds it, and scores it with
`dafter-wer`'s normalisation (`testdata/wer/README.md`). `--mode` overrides the
STT's mode option (transcribe, codemix), `--identify` has the STT identify the
language instead of being told it, as a session that switches languages does.
`--limit` scores only the first N clips.

`--set golden` scores the Vobiz golden set (`testdata/golden/`) instead of a
public sample: every clip of `--language`'s manifest (`--golden-root` points at
another one), each scored against both its native and romanized references
and its labelled entities. A golden run stops when a clip has no audio on
this machine.

`run` spends provider credits: it estimates the cost from
`python/dafter_runtime/src/dafter_runtime/prices.csv` first and refuses above
`--max-inr` (default 25). One Sarvam run of a 20-clip Kathbath sample is about
Rs 1.5.

## Report and baselines

The report gives, per clip and in total, WER with its substitutions,
deletions and insertions, CER, OIWER, entity accuracy by kind (golden set),
the languages the STT reported and, when it identified them, how many clips
it identified correctly, the time from the end of the clip's audio to its
last final, the audio seconds streamed and their cost. The metrics are
defined in `testdata/wer/README.md`.

`summary.primary` names the gated metric: WER for en, hi and mr, CER for te
and kn. `baselines.json` holds one recorded value per dataset, language,
provider, model, mode and hearing, and a `tolerance` (absolute, 0.02).
`run` compares against it (or `--baseline <file>`) and reports `held`,
`regressed` or `unrecorded`; it exits 1 only on `regressed`.
`--record-baseline` writes this run's value into the file. No baseline is
recorded yet.

Kathbath's references write English loanwords in the Indic script
(ಫೋಟೋ, पोस्ट). codemix mode writes them in Latin script (photo, post), so
against these references codemix is charged for words it heard right. The
report counts the Latin-script words each transcript holds (`latinWords`), the
most that script choice alone can have added to its errors; compare modes with
that in mind.

`python/dafter_evals/tests/test_accuracy.py` checks every committed sample
against `sources.json`.
