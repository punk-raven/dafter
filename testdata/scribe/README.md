# Scribe rule vectors

`rules.json` pins what the cross-field rules decide about the `scribe` block,
fed to both halves unchanged: `go/internal/config/scribe_test.go` and
`python/dafter_core/tests/test_scribe.py`.

Each case merges `patch` over `base` key by key at the top level; a `"$llm"`
value is replaced by `llm`, the one provider every case pins. `enabled` and
`pool` are what a valid document resolves to (the pool defaults to
`dafter-scribe`); `rejected` is the error code of the first broken rule and the
pointer of every broken rule, in the order the rule tables report them.

What the rules say: the scribe needs its own consent artifact, a sealed
session refuses it, it reads the live captions so it needs transcription
`live` or `both`, it needs an LLM, and it never shares the agent's pool.
