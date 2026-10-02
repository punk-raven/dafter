# Transcription rule vectors

`rules.json` pins what the cross-field rules decide about the `transcription`
block, fed to both halves unchanged:
`go/internal/config/transcription_test.go` and
`python/dafter_core/tests/test_transcription.py`.

Each case merges `patch` over `base` key by key at the top level; a
`"$batch"` value is replaced by `batch`, the one batch provider every case
pins. `mode` is the mode a valid document resolves to; `rejected` is the error
code of the first broken rule and the pointer of every broken rule, in the
order the rule tables report them.

What the rules say: transcription needs its own consent artifact, a sealed
session is never transcribed, live captions need the agent (its worker does
the transcribing), and the transcript after the call needs recording on with
the track layout and a pinned batch provider.
