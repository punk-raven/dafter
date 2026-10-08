---
paths:
  - "schemas/**/*.json"
  - "go/internal/config/*.go"
  - "go/internal/{configstore,configcheck,admin}/*.go"
  - "go/internal/{schema,events,errs}/*.go"
  - "go/tools/enumgen/*.go"
  - "python/dafter_core/src/dafter_core/*.py"
  - "python/dafter_core/tests/*.py"
  - "go/cmd/dafter-control/catalog.json"
  - "testdata/{config,events,agents}/**"
---

# Rules enforced in code: schemas and config

Identical on both halves (Go and Python) unless stated. Index:
`.agents/skills/project-context/SKILL.md`.

## Schemas and validation

- Validate the raw document, then decode: `config.Parse`, `events.Parse`; `config.parse`,
  `events.parse_event`.
- Reject unknown fields: schemas close with `unevaluatedProperties`; Go also uses
  `DisallowUnknownFields`.
- A schema file stays under 500 lines.
  - A family of event payloads goes in its own file under `schemas/events/v1/` (as
    `transcript.schema.json` and `scribe.schema.json`), applied from the envelope's `allOf` by
    `$ref`.
  - A config block goes under `schemas/config/v1/` (as `language-switching.schema.json`,
    `backchannel.schema.json`, `scribe.schema.json` and `telephony.schema.json`), referenced by its
    `$id`.
  - Go walks every embedded schema; Python needs the file in `_FILES` (`dafter_core/validation.py`).
- Error messages are safe to log (no name, email, phone, transcript):
  `schemas/errors/v1/error.schema.json`. Report every problem, located by JSON pointer.
- `errs.retryable` retries only provider unavailable, timeout, rate limited and stream closed; never
  auth or quota: a retry spends budget and delays the error reaching the caller.
- Enum drift is tested on both halves: `TestGeneratedEnumsMatchSchema` in each owning Go package and
  `python/dafter_core/tests/test_enums.py`. A Go package may have several test files, each cohesive
  to one area and under 500 lines.

## Cross-field rules

One table per half, `go/internal/config/rules.go` and `python/dafter_core/src/dafter_core/rules.py`;
every broken rule is reported with its pointer.

- Sealed forbids agent.
- An addressing mode that waits to be called needs a name; a near miss may not be the name or an
  alias.
- Recording needs a consent artifact.
- `session_create` start needs `room_composite` layout.
- `media.encryption.mode` must match what `privacyMode` implies.
- End-to-end encryption forbids recording (every egress layout is server-side).
- `provider_endpointing` forbids `turn.localVadEnabled`.
- `transcription.mode` other than `off` needs `transcription.consentArtifactId` and is forbidden
  when sealed. `live`/`both` needs the agent (its worker transcribes). `after_call`/`both` needs
  track recording and a `transcription.batch` provider. Vectors: `testdata/transcription/`.
- `agent.languageSwitching` enabled must list the session's own language.
- `scribe.enabled` needs `scribe.consentArtifactId`, transcription `live`/`both`, a `scribe.llm` and
  a pool other than the agent's; forbidden when sealed. Vectors: `testdata/scribe/`.
- Telephony forbids end-to-end encryption (at `/channel`). A recorded telephony session needs the
  agent (it tells the caller).
- `telephony.phoneGuests` other than `off` needs a trunk, forbids end-to-end encryption and is
  forbidden on the telephony channel (all at `/telephony/phoneGuests`). A recorded session with
  phone guests needs the agent. Vectors: `testdata/telephony/`.

## Config resolution

- Layers then axes, `go/internal/config/resolve.go`, in order: defaults; the named agent (`agents`
  kind, `go/internal/config/agents.go`; none named resolves as the defaults state); tenant; profile
  (the request's, else the agent's); each axis's `tuning` (language, then channel); session
  overrides; each axis's `overlay`.
- `tuning` holds what a session may change (e.g. turn strategy, turn constants); an override wins
  over it. `overlay` pins what the axis decides (e.g. the language's pipeline and batch
  transcription provider, the channel's media) and lands last.
- An override that an overlay would replace is refused, located by pointer, never dropped.
- Each focus language states its own route (STT, LLM, TTS voice) and turn constants. Semantic turn
  detection only where the on-device detector covers the language (`test_languages.py` reads the
  catalog).
- A session override may not name a stage's `credentialRef`, `options.endpoint` or
  `options.baseUrl`, or `telephony.trunk`: refused by pointer (`operatorOnlyProblems`), like the ids
  the control plane mints. This rule is on the override layer, not the resolved document; only Go
  resolves, so it has no Python half.
- Layers replace lists, except `/agent/addressing/nearMisses`, which every layer adds to
  (`unionNearMisses`).
- Config hash: RFC 8785, then SHA-256, with `configHash` stripped; `go/internal/config/hash.go` and
  `python/dafter_core/src/dafter_core/hashing.py`, both pinned to the vectors in `testdata/`. Change
  both together.

## Catalog

- SQLite, append-only revisions of whole documents per kind, published as numbered releases
  (`go/internal/configstore`).
- `catalog.json` seeds an empty store as release 1; its `defaults` and `llms` stay in the repository
  and are imported on start. Everything else is edited through the admin API (`go/internal/admin`,
  own listener `DAFTER_ADMIN_ADDR`, bearer `DAFTER_ADMIN_TOKEN`).
- Every write and publish passes `go/internal/configcheck`: document shape, pasted-secret screen,
  then a dry-run resolve of every affected tenant x agent x profile x language x channel.
- Running control planes poll and swap the live release in (`configstore.Live`). A session stores
  its `release_id` beside its hash.
