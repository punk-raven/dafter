# Dafter agent context

Dafter is a reusable realtime AI media toolkit: any application gets realtime
audio/video sessions with AI participants (talking agents, live translation,
live transcription, secure recording) through one API, one SDK and one config
document. Applications configure it; they never implement pipelines, talk to
model vendors or touch the media server.

Read `docs/dafter.md` first. It is the design and the delivery plan, written
as agent context:

- Section 1, problem statement: the consumer shapes the design is validated against.
- Section 2, definition: the fifteen key decisions and their rationale.
- Section 3, architecture: four planes, the hot path rule, the two channels,
  provider abstraction, configuration model, events.
- Section 4, features. Section 5, infrastructure, package layout and
  engineering standards. Section 6, the phased delivery plan (Phase 0, 1, 1b, ...).

## Repo layout

- `schemas/` is the only source of truth: the resolved config document, the
  event envelope, the error taxonomy and the shared identifier patterns. Every
  language type is generated from here, never hand-written per language.
- `go/` and `python/` are the two halves. Go holds the control plane, state,
  egress and seal; Python holds the agent runtime, providers, batch pass and
  evals. They touch only at the resolved config document and the event
  envelope, both defined in `schemas/`. Today: `go/internal/{ids,errs,schema,events,config}`
  and `python/dafter_core/`.
- `go/tools/enumgen` reads the schemas and emits enum constants for both halves.
- `Makefile` is the build entry point; `.github/workflows/ci.yml` runs it.

## Generated files: never hand-edit

`make generate` produces all of these; `make generate-check` fails the build if
any is stale or hand-edited (it runs first in `make check` and in CI):

- `go/internal/**/*_gen.go`
- `python/dafter_core/src/dafter_core/enums.py`
- the two schema copies, `go/internal/schema/schemas/` and
  `python/dafter_core/src/dafter_core/_schemas/` (Go cannot `go:embed` across a
  module boundary, so the copy is checked in; see the Makefile comment)

Changing anything under `schemas/` means running `make generate` and committing
the output in the same commit. Follow the `schema-change` skill.

## Commands

`make check` is what CI runs. Its parts, all defined in `Makefile`:

- `make generate` / `make generate-check` (codegen and drift policing)
- Go: `make build`, `make vet`, `make lint` (golangci-lint, built into `go/bin`),
  `make test` (race detector), `make tidy`
- Python: `make py-lint` (ruff check, ruff format check, mypy strict),
  `make py-test` (pytest). Both run through `uv run --frozen` from `python/`.

Prerequisites are `go` (toolchain pinned in `go/go.mod`) and `uv`. CI is four
jobs (generated, go, python 3.11 to 3.13, govulncheck); see the workflow file.

## Rules enforced in code, preserved on both halves

Each of these exists in Go and in Python and the two must keep giving the same
answer to the same document. The commit bodies on `git log` explain why each
was introduced; do not weaken one side without the other.

- Validate the raw document against the schema, then decode. Decoding first
  drops unknown keys before the schema sees them. Go: `config.Parse` and
  `events.Parse`; Python: `config.parse` and `events.parse_event`.
- Unknown fields are rejected. Schemas close with `unevaluatedProperties`; Go
  additionally decodes with `DisallowUnknownFields`.
- Three cross-field checks the schema cannot express, in
  `go/internal/config/config.go` (`check`) and
  `python/dafter_core/src/dafter_core/config.py` (`ResolvedSessionConfig.check`):
  a sealed session forbids an agent; recording requires a consent artifact;
  recording that starts at `session_create` requires the `room_composite` layout.
- Error messages must be safe to log: no name, email, phone or transcript
  content. Stated in `schemas/errors/v1/error.schema.json` and on the error
  type in both halves. Every validation failure is reported, each located by
  JSON pointer, with the schema named by `$id`.
- Identifiers are opaque patterns (`schemas/common/v1/ids.schema.json`), minted
  in `go/internal/ids`. Credentials are `secret://` references, never values.
  Region tokens are opaque and provider scoped.
- Enum drift is tested, not reviewed: `go/internal/*/enums_test.go` and
  `python/dafter_core/tests/test_enums.py` hold every generated enum to its
  schema. A new enum needs a case in both.

## Package dependency graph

Enforced by depguard in `go/.golangci.yml`, which is also where the intended
graph is documented (`ids` and `errs` are leaves; `schema` depends on `errs`;
`events` and `config` depend on `errs` and `schema` and not on each other).
The same file denies the media server SDK outside the transport package.
The wider target graph is in `docs/dafter.md`, section 5.

## Commit convention

Conventional Commits: `type(scope): subject` in the imperative, with a body that
explains why the change was made and what was verified. Run
`git log origin/main..HEAD` for the examples that set the bar; the bodies are
where the reasoning behind each decision lives.

## Skills

- `.agents/skills/schema-change/`: checklist for changing anything under `schemas/`.
- `.agents/skills/add-provider/`: the fixed checklist for adding a provider
  (Phase 1 / 1b of the delivery plan).

`.claude/skills` is a symlink to `.agents/skills`.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
