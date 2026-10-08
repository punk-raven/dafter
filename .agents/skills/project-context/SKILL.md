---
name: project-context
description: >-
  Dafter project context: repository layout, generated files, dependency graph, commit format,
  commands, dev stack setup, and the rules enforced in code. Load before working on any code,
  config, schema, test, command, dev stack or commit in this repo: the Go control plane, state,
  egress and transport (LiveKit, SIP, Vobiz telephony), the Python agent runtime, providers
  (Sarvam, OpenAI-compatible LLMs), batch transcription, scribe and evals, the JSON schemas and
  config catalog, the browser test client, docker compose, Grafana or Prometheus.
---

# Dafter project context

Realtime AI media toolkit (agents, translation, transcription, sealed recording) behind one API, one
SDK, one config document. Read `docs/dafter.md` first; section 6 is the delivery plan.

## Skill files

- `context.md`: layout, generated files, dependency graph, commit format. Read before any change.
- `commands.md`: build, lint, test, evals, ASR, scorecard, load tests, Grafana dashboards. Read
  before running or adding a command.

## Area rules: `.agents/rules/`

Claude Code loads each file automatically (`.claude/rules` symlinks to `.agents/rules`) when a file
matching its `paths:` frontmatter is read or edited. Other agents read the matching file by hand
before touching the named area.

- `stack.md`: first run, dev stack, compose services, ports, MinIO, SFU.
- `worker-events.md`: `dafter.events` envelopes, Prometheus metrics, turn timing fields.
- `test-client.md`: test client files, layout, e2ee client pin.
- `config.md`: schemas, validation, cross-field rules, config resolution, config hash, catalog.
- `providers.md`: LLM routes, OpenAI-compatible endpoints, credential refs.
- `security.md`: e2ee keys, token grants, participant ids.
- `control.md`: session storage, dispatch, agent start/stop, recording, webhooks, noise filters.
- `agent.md`: worker plan and refusals, agent identity, own voice, addressing, tools.
- `speech.md`: Sarvam specifics, backchannel, speech planning, voice styles, fillers.
- `languages.md`: personas per focus language, live language switching.
- `transcription.md`: live captions, transcript of record, the scribe.
- `telephony.md`: outbound calls, phone guests, Vobiz inbound, meeting dial-in.

## Skills: `.agents/skills/`

Canonical files live in `.agents/skills/`. Vendored skills (listed in `skills-lock.json`, installed
by the skills CLI) are never edited; `skills update` rewrites them. Claude Code sees a skill only
through a per-skill relative symlink in one scope:

- `.claude/skills/`: listed every session. project-context, schema-change, add-provider,
  reading-livekit-docs.
- `go/.claude/skills/`: listed once a file under `go/` is read or edited. The Go skills that apply
  to this repo (golang-how-to, -code-style, -naming, -error-handling, -testing, -lint and others).
- `python/.claude/skills/`: listed once a file under `python/` is read or edited. The LiveKit agent
  skills (building, debugging, testing, operating, simulations, scenarios).
- `.claude/skills/` plus `skillOverrides: user-invocable-only` in `.claude/settings.json`: callable
  by `/name` only. golang-documentation (doc comments conflict with agent rule 6),
  -dependency-injection, -stay-updated.

Many vendored Go skills also carry `paths: "**/*.go"`, which keeps them unlisted until a `.go` file
is touched. A new vendored skill: install it, then symlink it into the narrowest matching scope, or
into `.claude/skills/` with a `skillOverrides` entry. Codex reads `.agents/skills/` directly,
unscoped. golang-how-to's `configure` action writes force-triggers into `AGENTS.md`; do not run it.

## Guardrails

Checks live in `scripts/agent_rules/`; Claude Code runs them from hooks and permissions in
`.claude/settings.json`; `make rules-check` (part of `make check` and CI) runs them for every agent.

## Maintaining these files

`AGENTS.md` (symlinked as `CLAUDE.md`) holds only the agent rules. Repo-wide knowledge goes in this
skill; knowledge for one area goes in `.agents/rules/<area>.md`.
A rule file needs a `paths:` frontmatter (YAML list of quoted globs, `**` and `{a,b}` allowed) whose
globs match existing files. A rule file without `paths` loads on every session.
Keep these files for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
Keep entries terse and objective: facts and imperative rules, no rationale unless it prevents a
mistake.
Wrap lines at about 100 characters; each file stays at or under 100 lines.
