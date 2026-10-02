# Event vectors

One envelope per typed payload the agent worker publishes, each a document the
worker could publish on `dafter.events`. Both halves parse them
and must accept them, then refuse the same mutations of them:

- `go/internal/events/events_test.go`
- `python/dafter_core/tests/test_events.py`

`agent-state-changed.json` is the state change of an agent that waits to be
called by name, woken by participant `p_4b81e0d7` saying its name and now
thinking. Awake, it must say who woke it and how; dormant, it must not name
anyone; and who woke it is an opaque participant id, never a name.

`agent-turn-metrics.json` is one agent turn's latency split into its layers, in
milliseconds. `endpointMs` (the provider holding end of speech after the user's
last voiced audio) comes before every framework layer, so `replyGapMs`, from
that audio to the agent speaking, is `endpointMs` plus `e2eLatencyMs` here. Its first sentence reached TTS 287 ms after the LLM's first
token, inside the 500 ms the serial rule allows, so `serial` is false; a
`serial` without both LLM layers is refused. It was a reply in Kannada in a
session that switches languages, so it names its `language`, a language tag.

`session-usage.json` is a session's consumption with one priced item and one
unpriced one. An unpriced item carries no `costInr` at all, and `costInr` on
the payload sums only the priced items, so a consumer can tell "free" from
"no price known". The numbers are illustrative, not a statement of any
provider's price.

`agent-configured.json` is what a worker announces when its session starts:
the LLM it built (named as its usage is reported) and the effective state of
fillers, backchannel handling and number normalization. A provider in vendor
spelling, or a normalization that is not a mode, is refused.

`provider-degraded.json` is an OpenRouter LLM refusing a request with HTTP 429
during a call, which the framework will retry. It carries the error document
from `schemas/errors/v1/`, so the page can say why the agent went quiet.

`transcript-partial.json` and `transcript-final.json` are live captions: a
human's segment still being recognised, labelled by participant id and the
recognizer that heard it, and the agent's own line once spoken, which names no
participant and no recognizer. A human line must name the participant, an
agent line must not, and a segment id is opaque. They carry what was said,
which is why they are never logged; the schema says why the text is allowed
there at all.

`transcript-version-created.json` is one version of a transcript of record,
made after the call from two participants' own recorded tracks, in its
verbatim and clean renderings, with how it was made (provider, model, the
session's `configHash`, the recordings and whose they are, when). Its
`transcriptHash` is SHA-256 over RFC 8785 of the payload with that field
omitted, and both halves pin it:
`4010587f3b7e5c5efc778288684a658a53c3266c6a572909d1b157b1d44efe9a`, in
`go/internal/config/hash_test.go` and
`python/dafter_core/tests/test_transcript_events.py`. The config hash in it is
illustrative. `go/internal/events/transcript_test.go` and the same Python file
refuse the same mutations of all three.
