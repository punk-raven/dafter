# Event vectors

One envelope per event type the agent worker added for measuring a call, each
a document the worker could publish on `dafter.events`. Both halves parse them
and must accept them, then refuse the same mutations of them:

- `go/internal/events/events_test.go`
- `python/dafter_core/tests/test_events.py`

`agent-turn-metrics.json` is one agent turn's latency split into its layers, in
milliseconds.

`session-usage.json` is a session's consumption with one priced item and one
unpriced one. An unpriced item carries no `costInr` at all, and `costInr` on
the payload sums only the priced items, so a consumer can tell "free" from
"no price known". The numbers are illustrative, not a statement of any
provider's price.
