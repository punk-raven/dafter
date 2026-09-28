# Public speech samples for word error rate per language

`uv run dafter-asr` (from `python/`, `python/dafter_evals/src/dafter_evals/asr.py`)
measures the word error rate of the STT the worker builds, language by
language, on public recordings, before any live call. The samples are small
and pinned; the audio is never committed.

## What is pinned

`sources.json` pins each source:

- **Kathbath** (kn-IN, hi, mr-IN, te-IN), read speech from AI4Bharat
  (https://arxiv.org/abs/2208.11761), CC-BY-4.0, as the Vistaar benchmark
  packages its test sets (https://github.com/AI4Bharat/vistaar): one zip,
  pinned by its URL, byte size and ETag. `dafter-asr` reads only the members it
  needs from it by HTTP range, so pinning a language downloads about 6 MB, not
  the 3.5 GB archive, and a changed archive is refused.
- **Svarah** (en-IN), Indian-accented English from AI4Bharat
  (https://arxiv.org/abs/2305.15760), CC-BY-4.0, pinned by its Hugging Face
  revision. It is gated: pinning or fetching it needs `HF_TOKEN` from an
  account that has accepted the dataset's terms on
  https://huggingface.co/datasets/ai4bharat/Svarah (automatic approval).
  Rows and audio come from the Hugging Face datasets server, which
  rate-limits a pin partway through; `dafter-asr` retries a 429 or 5xx with
  backoff and stops at once on any other refusal.

`<dataset>-<language>.json` is one pinned sample: the source it came from,
the selection rule, and per clip its id, its member in the source, its
speaker, duration, sha256 and the dataset's reference transcript. The rule:
rows 3 to 12 s long, in path order, one clip per speaker, taken in turn from
each gender, 20 clips. Each committed Kathbath sample is 20 speakers, 10 of
each gender, 136 to 178 s of audio. Svarah names no speaker, so its sample
takes one clip per first language and district instead: 20 of them, 124 s of
audio.

## Commands

    uv run dafter-asr fetch ../testdata/asr/kathbath-kn-IN.json
    uv run dafter-asr run ../testdata/asr/kathbath-kn-IN.json \
      --job ../testdata/agent/kannada-webrtc-job.json --out kn.json

`fetch` downloads a sample's clips into `testdata/asr/audio/` (git-ignored)
and refuses any whose sha256 differs. `run` streams each clip in real time,
followed by 1.5 s of silence, through the STT the job vector names, built by
the providers' registry exactly as the worker builds it, and scores it with
`dafter-wer`'s normalisation (`testdata/wer/README.md`). `--mode` overrides the
STT's mode option (transcribe, codemix), `--identify` has the STT identify the
language instead of being told it, as a session that switches languages does.
`run` spends provider credits: it estimates the cost from
`python/dafter_runtime/src/dafter_runtime/prices.csv` first and refuses above
`--max-inr` (default 25). One Sarvam run of a 20-clip Kathbath sample is about
Rs 1.5.

The report gives, per clip and in total, the word error rate and its
substitutions, deletions and insertions, the languages the STT reported
and, when it identified them, how many clips it identified correctly, the
time from the end of the clip's audio to its last final, the audio seconds
streamed and their cost.

Kathbath's references write English loanwords in the Indic script
(ಫೋಟೋ, पोस्ट). codemix mode writes them in Latin script (photo, post), so
against these references codemix is charged for words it heard right. The
report counts the Latin-script words each transcript holds (`latinWords`), the
most that script choice alone can have added to its errors; compare modes with
that in mind.

`python/dafter_evals/tests/test_accuracy.py` checks every committed sample
against `sources.json`.
