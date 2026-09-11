# Dafter

[![ci](https://github.com/punk-raven/dafter/actions/workflows/ci.yml/badge.svg)](https://github.com/punk-raven/dafter/actions/workflows/ci.yml)

A reusable realtime AI media toolkit. Dafter gives any application realtime
audio and video sessions with AI participants: talking agents, live
translation, live transcription and sealed, consented recording, behind one
API, one client SDK and one configuration document. Applications configure
it; they do not build pipelines, integrate model vendors or touch the media
server. Everything that varies between applications is configuration or an
extension point, never a fork.

The full design, including the problem statement, the key decisions, the
architecture and the phased delivery plan, is in [docs/dafter.md](docs/dafter.md).

## Status

Pre-alpha. The repository is in Phase 0 and Phase 1 of the delivery plan
(`docs/dafter.md`, section 6): validating the risky assumptions and laying the
foundations. Nothing here serves traffic yet, and every API, schema and
package boundary is unstable and may change without notice.

## Layout

- `docs/` - the design document; the single source for the "why".
- `schemas/` - JSON Schema for the config document, the event envelope, the
  error taxonomy and identifiers. The only source of truth for shared types.
- `go/` - the control plane, state, egress and seal, plus the code generator
  in `go/tools/enumgen` and the linter module in `go/tools/golangci`.
- `python/` - the agent runtime, providers, batch pass and evals, as a `uv`
  workspace starting with `dafter_core`.
- `.github/` - CI workflow and Dependabot configuration.
- `.agents/` - agent skills (`schema-change`, `add-provider`); `AGENTS.md` at
  the root is the operating summary for agent sessions.

## Build and test

Two prerequisites, both single binaries:

- `go` - `go/go.mod` pins the toolchain, so any Go 1.21 or newer fetches the
  right version on first use.
- `uv` - manages the Python interpreter, the virtualenv and every dev
  dependency, from the committed `python/uv.lock`.

```sh
make check          # everything CI runs
make generate       # refresh the outputs derived from schemas/
make build vet lint test tidy   # Go targets
make py-lint py-test            # Python: ruff, mypy strict, pytest
make help           # list every target
```

`make check` runs `generate-check` first and fails if any generated output is
stale or was hand-edited.

## How the two halves relate

The Go and Python halves touch only at the resolved config document and the
event envelope, and both of those are defined once, in `schemas/`. From the
schemas, `make generate` produces the enum constants for both languages and
copies the schemas into each module so they can be embedded and validated at
startup. Generated files are committed with the schema change that produced
them and are never edited by hand. Every rule the schema cannot express is
implemented identically on both sides and tested with the same documents, so
a given input gets the same answer, and the same error code, from either
half.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup, the commit convention and
what a pull request needs. Security issues go through
[SECURITY.md](SECURITY.md), not the issue tracker.

## License

MIT. See [LICENSE](LICENSE).
