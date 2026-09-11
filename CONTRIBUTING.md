# Contributing to Dafter

## Setup

Install `go` and `uv`; nothing else is needed. `go/go.mod` pins the Go
toolchain and `python/uv.lock` pins every Python dependency, so a clean
checkout reproduces exactly what CI runs.

```sh
git clone https://github.com/punk-raven/dafter.git
cd dafter
make check
```

`make help` lists every target. The Go and Python halves can be checked
separately with `make vet lint test` and `make py-lint py-test`.

## Schemas first, generated files never

`schemas/` is the only source of truth for shared types. Anything under
`go/internal/schema/schemas/`, `python/dafter_core/src/dafter_core/_schemas/`,
`*_gen.go` and `enums.py` is produced by `make generate` and must not be
edited by hand; `make generate-check` is the first step of `make check` and
fails the build if it drifts. The full procedure for a schema change,
including the hand-written parsers and drift tests on both sides, is the
`schema-change` skill in `.agents/skills/schema-change/SKILL.md`. `AGENTS.md`
at the repository root summarises the rules both halves must keep identical.

## Commits

Conventional Commits, `type(scope): subject`, imperative mood, subject under
about 70 characters. The body explains why the change exists and what was
verified, because the reasoning behind a decision lives in the commit rather
than in a comment. The branch history is the reference; three examples:

- `fix(config): validate the document, then decode it`
- `feat(python): generate the enums instead of hand-writing them`
- `ci: run the checks on every pull request`

Read those bodies with `git log` before writing your first one. Each states
the defect or gap, how it was found, what changed, and how it was verified.

## Pull requests

- One logical change per PR. A refactor and a behaviour change are two PRs.
- CI must be green: generated output current, Go vet, lint and race tests,
  Python lint, types and tests on every supported version, govulncheck.
- The description says what and why, following the pull request template.
  If a schema changed, the generated output is in the same PR.
- Update `docs/dafter.md`, `AGENTS.md` or a skill when a change alters a
  rule they state.

## Licensing of contributions

By contributing you agree that your contributions are licensed under the
MIT License in `LICENSE`. No Developer Certificate of Origin sign-off and
no Contributor License Agreement is required.
