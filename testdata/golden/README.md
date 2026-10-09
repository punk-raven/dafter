# Vobiz golden set

The one dataset that matches what callers actually sound like: real 8 kHz G.711 calls over Vobiz,
code-mix, Indian background noise. Target: 1 to 2 hours per language plus a code-mix slice.
`dafter_evals.golden.load(language)` (`python/dafter_evals/src/dafter_evals/golden.py`) reads one
language's manifest; every eval that needs real call audio (ASR scoring, turn and noise sweeps)
reads clips through it.

No clip exists yet. Every manifest is pinned with an empty `clips` list until consented audio has
been captured and labelled.

## Files

- `<language>.json`: one manifest per language: `en-IN`, `hi`, `te-IN`, `kn-IN`, `mr-IN`.
- `manifest.schema.json`: JSON Schema for a manifest. The loader enforces the same rules and raises
  `ValueError` on a malformed manifest; a missing manifest loads as no clips.
- `example.json`: one illustrative `hi` clip showing every field. No audio stands behind it and no
  eval reads it.
- `audio/<language>/<clip>.wav`: the audio. Git-ignored, never committed.

## Consent

Every clip is a real person's voice and is used only with their consent, collected from day one.

- A clip comes from a call where the caller heard the recording notice
  (`telephony.recordingNotice`, `always` by default) and agreed to the recording being kept for
  evaluation, or from a scripted speaker who signed a consent form.
- `consentId` on each clip points to that consent record, kept outside this repo. A clip without
  one is refused by the loader.
- A withdrawn consent removes the clip from the manifest and its audio from every machine that
  holds it.
- The audio lives only under `testdata/golden/audio/`, which is git-ignored, and is shared out of
  band, never through git.

Retention period and the exact consent wording are an open decision; no clip is added until it is
made.

## Capture path

1. A consented call runs through the real phone path: Vobiz SIP into LiveKit, with
   `recording.tracks` on so each voice is recorded on its own track (see
   `.agents/rules/telephony.md` and `.agents/rules/control.md`). Scripted speakers call in the same
   way, so their audio carries the same codec and line.
2. The caller's track is exported, cut into clips of one turn each and kept at 8 kHz mono
   (`channel: telephony_8k`). Audio recorded on a wideband path is tagged `wideband`.
3. Each clip is saved as `audio/<language>/<clip>.wav` and gets a manifest entry.

## Sampling loop

Production failures feed this set. The scribe's judge (`python/dafter_scribe/judging.py`, the
`dafter-screen` criteria) scores a sample of live agent turns: `scribe.scoring.sampleRate`, or
`languageSampleRates` per base language, both 0 by default, capped at `maxTurnsPerSession` turns.
With `scribe.scoring.keepFailures` on and `DAFTER_SCRIBE_REVIEW_DIR` set on the scribe worker,
each turn with a `fail` verdict is appended to `<dir>/<base language>/<session>.jsonl`: what the
caller said, the reply, the verdicts, the config version and arm. Audio is only referenced
(consent id, participant, caption segments, wall-clock span), and only when the session's recording
has a consent artifact; the review queue never holds audio.

Each week, from `python/`:

1. `uv run dafter-scribe-export golden --queue <dir> --language <base> --out <file>`
   writes `manifest` (candidate clips in this manifest format, `annotators` empty) and `cuts`
   (where to cut each clip from the caller's track recording). Turns without consented audio are
   left out.
2. Cut each clip as in the capture path, label it as below, add both annotators, and move the clip
   into `<language>.json`. The loader refuses it until two annotators are named.
3. `uv run dafter-scribe-export bank --queue <dir> --language <base> --out <file>`
   writes the language's screen bank with each failed question added once as `<base>-r<segment>`;
   review it and replace `python/dafter_evals/src/dafter_evals/screen/banks/<base>.json` with it.

Delete the queue files once they are exported.

## Labelling

Two annotators label every clip independently; `annotators` lists both ids and the loader refuses
a clip with fewer than two distinct ones. Disagreements are settled by a third listener before the
clip is added.

Per clip:

- `reference.native`: what was said, in the language's native script, words as spoken.
- `reference.romanized`: the same in Latin script, the way code-mix speakers write it; `null` when
  not labelled.
- `entities`: `number`, `phone`, `amount`, `name`, `address` or `date` spans, as character offsets
  into `reference.native` (`startChar` inclusive, `endChar` exclusive); `text` must equal that
  slice.
- `labels`: tags that describe the clip as a whole, used to slice later sweeps:
  - `turn:hold` or `turn:end`: whether the silence the clip ends on (100 ms or longer) is a pause
    inside the turn or the end of it.
  - `backchannel:<class>` and `interruption:<class>`: the class of a backchannel or barge-in the
    clip carries (for example `backchannel:acknowledge`, `interruption:correction`).
  - `noise:<tag>` and `voice:<tag>`: recording conditions (for example `noise:traffic`,
    `noise:tv`, `voice:female_adult`, `voice:elderly`).
  - `codemix`: the clip mixes languages.
  Class and tag values are lowercase snake case.

## Native review

`nativeReview.status` records whether a native speaker has reviewed the language's references:
`pending`, `in_review` or `approved` (which must name `reviewers`).

| Language | Clips | Native review |
|----------|-------|---------------|
| en-IN    | 0     | pending       |
| hi       | 0     | pending       |
| te-IN    | 0     | pending       |
| kn-IN    | 0     | pending       |
| mr-IN    | 0     | pending       |
