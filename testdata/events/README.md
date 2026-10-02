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
`serial` without both LLM layers is refused.

`session-usage.json` is a session's consumption with one priced item and one
unpriced one. An unpriced item carries no `costInr` at all, and `costInr` on
the payload sums only the priced items, so a consumer can tell "free" from
"no price known". The numbers are illustrative, not a statement of any
provider's price.
