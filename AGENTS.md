# Dafter agent context

Realtime AI media toolkit (agents, translation, transcription, sealed recording) behind one API, one SDK, one config document. Read `docs/dafter.md` first; section 6 is the delivery plan.

## Layout

- `schemas/`: the only source of truth. All language types are generated from it.
- `go/` (control plane, state, egress, seal) and `python/` (agent runtime, providers, batch, evals). They touch only at the resolved config document and the event envelope.
- `go/tools/enumgen`: emits enum constants for both halves. `Makefile` is the entry point; `.github/workflows/ci.yml` runs it.

## Generated files: never hand-edit, never commit

Produced by `make generate`, git-ignored, policed by `make generate-check` (first step of `make check`):
`go/internal/**/*_gen.go`, `python/dafter_core/src/dafter_core/enums.py`, `go/internal/schema/schemas/`, `python/dafter_core/src/dafter_core/_schemas/`.
Every target regenerates before it runs, so a clone only needs `./scripts/setup.sh` once. See the `schema-change` skill.

## Commands

- `./scripts/setup.sh` once per clone (a fresh checkout does not compile until it runs)
- `make check` (the Go and Python checks CI runs)
- Go: `make build vet lint test tidy`
- Python: `make py-lint py-test` (ruff, mypy strict, pytest via `uv run --frozen` from `python/`)

## Rules enforced in code, kept identical on both halves

- Validate the raw document, then decode: `config.Parse`, `events.Parse`; `config.parse`, `events.parse_event`.
- Unknown fields rejected: schemas close with `unevaluatedProperties`; Go also uses `DisallowUnknownFields`.
- Cross-field rules the schema cannot express are one table per half, `go/internal/config/rules.go` and `python/dafter_core/src/dafter_core/rules.py`, every broken rule reported with its pointer: sealed forbids agent; recording needs consent artifact; `session_create` start needs `room_composite` layout.
- Error messages safe to log (no name, email, phone, transcript): `schemas/errors/v1/error.schema.json`. Report every problem, located by JSON pointer.
- Identifiers are opaque patterns (`schemas/common/v1/ids.schema.json`, minted in `go/internal/ids`); credentials are `secret://` refs; region tokens opaque.
- Config resolution is layers then axes, `go/internal/config/resolve.go`: defaults, tenant, profile, session overrides, then the language and channel overlays. The overlays land last, so a channel overlay wins over a session override.
- Enum drift tested on both halves: `TestGeneratedEnumsMatchSchema` in each owning Go package and `python/dafter_core/tests/test_enums.py`; each Go package carries at most one test file, named after the package.

## Dependency graph

depguard in `go/.golangci.yml` (also documents the graph and denies the media server SDK outside transport). Target graph: `docs/dafter.md` section 5.

## Commits

Conventional Commits `type(scope): subject`, imperative, body explains why and what was verified. Examples: `git log origin/main..HEAD`.

## Skills

`.agents/skills/schema-change/`, `.agents/skills/add-provider/`. `.claude/skills` symlinks to `.agents/skills`.

## Agent rules

These override default behavior. Project config wins on conflict with global rules; otherwise both apply.

1. **Tool only.** Do only the task given. No suggestions, follow-ups, interpretations, advice, or assumptions. Do not automatically initiate tests, dev servers, migrations, or anything not explicitly instructed.
2. **User-level rules followed religiously.** Every rule in the user's global config (`~/.claude/CLAUDE.md`, `RULES.md`, `TOOLING.md`) must be followed without mistakes.
3. **Project files stay in this folder and are gitignored.** All project-related memory, temp files, and scratchpad are maintained inside this folder only, gitignored.
4. **Tooling routing.** Plans through `lavish-axi`. Tasks through `tasks-axi`. GitHub through `gh-axi`. All tasks are subagent-driven on a new terminal, never on the main thread.
5. **Explicit bypass is single-message only.** A rule bypass applies only to the current message. On the next message, all rules apply again. A bypass never carries over.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
