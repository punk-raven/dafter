# Repository context

## Layout

- `schemas/`: the single source of truth; all language types are generated from it.
- `go/`: control plane, state, egress, seal. `python/`: agent runtime, providers, batch, evals. They
  meet only at the resolved config document and the event envelope.
- `python/` workspace members:
  - `dafter_core`: generated contract.
  - `dafter_providers`: one subpackage per vendor; `registry.py` maps
    `agent.pipeline.<stage>.provider` to a factory. Nothing else imports a vendor plugin.
  - `dafter_runtime`: the agent worker, pool `dafter-py`.
  - `dafter_evals`: scripted live-agent harness (`uv run dafter-evals`) and `uv run dafter-wer`; see
    `commands.md`.
  - `dafter_batch`: the transcript after the call (`uv run dafter-batch <session>`).
  - `dafter_scribe`: the silent scribe, pool `dafter-scribe` (`uv run dafter-scribe start`); notes,
    turn scores and minutes from the live captions.
- `go/tools/enumgen`: emits enum constants for both halves. `Makefile` is the entry point;
  `.github/workflows/ci.yml` runs it on pushes to main and on pull requests only while they carry
  the `run-ci` label (Dependabot's too); merging a pull request cancels its runs.
- `testdata/`: fixtures read by both halves, so a cross-language claim is checked on both sides.
  Each directory has a README stating what it pins. `testdata/rfc8785/` is vendored verbatim: never
  edit or reformat it.

## Generated files: never hand-edit, never commit

Produced by `make generate`, git-ignored, checked by `make generate-check` (first step of
`make check`):

- `go/internal/**/*_gen.go`
- `python/dafter_core/src/dafter_core/enums.py`
- `go/internal/schema/schemas/`
- `python/dafter_core/src/dafter_core/_schemas/`

Every target regenerates before it runs; a clone needs only `./scripts/setup.sh` once. See the
`schema-change` skill.

## Dependency graph

- depguard in `go/.golangci.yml` enforces the whole graph: core below everything, state and
  transport as siblings above it, control above them.
- `github.com/livekit/*` is denied outside `go/internal/transport`; every media server concern goes
  behind it.
- A carrier's webhook dialect lives in `go/internal/carrier/<carrier>`, beside transport, below
  control.
- Target graph: `docs/dafter.md` section 5.

## Commits

Conventional Commits `type(scope): subject`, imperative; the body states why and what was verified.
Examples: `git log origin/main..HEAD`.
