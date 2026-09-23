# Dafter agent context

Realtime AI media toolkit (agents, translation, transcription, sealed recording) behind one API, one SDK, one config document. Read `docs/dafter.md` first; section 6 is the delivery plan.

## Layout

- `schemas/`: the only source of truth. All language types are generated from it.
- `go/` (control plane, state, egress, seal) and `python/` (agent runtime, providers, batch, evals). They touch only at the resolved config document and the event envelope.
- `go/tools/enumgen`: emits enum constants for both halves. `Makefile` is the entry point; `.github/workflows/ci.yml` runs it.
- `testdata/`: fixtures both halves read, so a cross-language claim is checked on both sides rather than asserted on one. Each directory has a README saying what it pins; `testdata/rfc8785/` is vendored verbatim and must never be edited or reformatted.

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
- Cross-field rules the schema cannot express are one table per half, `go/internal/config/rules.go` and `python/dafter_core/src/dafter_core/rules.py`, every broken rule reported with its pointer: sealed forbids agent; recording needs consent artifact; `session_create` start needs `room_composite` layout; `media.encryption.mode` must match what `privacyMode` implies; end-to-end encryption forbids recording, because every egress layout is server-side.
- `privacyMode` decides encryption: resolution stamps `media.encryption` so the hashed document states it. Under `e2ee` the control plane mints one shared key per session and returns it as `encryptionKey` beside the token, never inside the resolved document, which is hashed, stored and handed to every joiner. Who receives it derives from the role and the mode like a token grant, never from the request: `DisclosesKeyTo` in `go/internal/config/config.go`.
- Error messages safe to log (no name, email, phone, transcript): `schemas/errors/v1/error.schema.json`. Report every problem, located by JSON pointer.
- Identifiers are opaque patterns (`schemas/common/v1/ids.schema.json`, minted in `go/internal/ids`); credentials are `secret://` refs; region tokens opaque. Real credentials reach the process through the environment only.
- Config resolution is layers then axes, `go/internal/config/resolve.go`: defaults, tenant, profile, session overrides, then the language and channel overlays. The overlays land last, so a channel overlay wins over a session override.
- The config hash is RFC 8785 then SHA-256 with `configHash` stripped, mirrored in `go/internal/config/hash.go` and `python/dafter_core/src/dafter_core/hashing.py`. Both halves are pinned to the vectors in `testdata/`; changing either without the other fails on its own side.
- Token grants derive from the role and never from a client request, and no role is ever issued a room-admin, room-create, room-list, room-record or ingress grant: `grantsFor` in `go/internal/transport/livekit.go`. Every permission is stated rather than left unset, because the media server grants an absent permission by default. The one token that carries `roomRecord` or `roomCreate` is the per-call service token in `go/internal/transport/egress.go`, a separate claims type that is never returned to a client.
- Recordings are started and stopped by the control plane from the session's stored config (`POST /sessions/{id}/recording/start|stop`, `go/internal/control/recording.go`) over the media server's Twirp JSON API, no vendor SDK. Request JSON is pinned in `go/internal/transport/testdata/egress/`; the server answers with proto field names (`egress_id`).
- The binary serves `MetricsHandler`, not `Handler`: a route must be registered in `go/internal/control/metrics.go` too, and the control tests drive that mux.
- The noise filter a session resolves to is applied by the publishing client from one registry, `NOISE_FILTERS` in `go/cmd/dafter-control/testclient.html`: a member of `noiseCancellation` needs an entry there or it does nothing. The browser's own suppression and a processor are never both on, because stacking suppressors degrades speech; echo cancellation is separate and stays on. A processor is attached to the microphone track before it is published, never after. Worklet and WASM assets come from a pinned CDN URL beside the SDK pin, and every way loading them can fail falls back to `native` visibly.
- A session is written to the store before its token is minted. A token issued for a room whose config was never stored lets a worker join a session nobody can explain afterwards.
- Enum drift tested on both halves: `TestGeneratedEnumsMatchSchema` in each owning Go package and `python/dafter_core/tests/test_enums.py`; each Go package carries at most one test file, named after the package.

## Dependency graph

depguard in `go/.golangci.yml` holds the whole graph: core below everything, state and transport siblings above it, control above them. `github.com/livekit/*` is denied outside `go/internal/transport`, which is the seam every media server concern goes behind. Target graph: `docs/dafter.md` section 5.

## Dev stack

`make dev` from a clean clone builds and starts everything: control plane, LiveKit SFU, Redis, MinIO (recordings), Jaeger (tracing), Prometheus, Grafana, and CF tunnel (if configured). `make dev-down` tears it down. Config lives in `deploy/livekit.yaml` and `deploy/egress.yaml`; dev credentials are `devkey`/`secret`. The control plane is at `http://127.0.0.1:8080`. Recording storage reaches the control plane as `DAFTER_EGRESS_S3_*` (set in `docker-compose.yml` to match `deploy/egress.yaml`); without a bucket it boots and refuses recording starts. The egress container is on the host network, so it reaches the SFU and MinIO at `127.0.0.1`.

The SFU advertises `--node-ip` (`LIVEKIT_NODE_IP`, default 127.0.0.1) on the one published UDP port; anything else in `rtc` (a port range, `use_external_ip`) breaks local media, see the comments in `deploy/livekit.yaml`. LiveKit metrics are on port 6789, not 7880.

Load tests: `make loadtest` (HTTP only, token minting) and `make loadtest-media` (real WebRTC participants through `lk load-test`, knobs and pass criteria in `scripts/loadtest-media.sh`). `lk` must be the release binary `make tools` fetches into `go/bin`; a go-installed one embeds Git LFS pointers instead of video and publishes nothing. Grafana dashboard: `deploy/grafana/dashboards/dafter.json` (uid `dafter`); a uid change needs the grafana container recreated.

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
6. **No comments in code.** Names and structure carry the context. Tool directives (`//go:`, `//nolint`, `# noqa`, `# type:`, shebangs, generated-code headers) are the only exception.
7. **500 lines per file, hard cap.** A file that would pass 500 lines is split before it does.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.
