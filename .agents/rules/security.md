---
paths:
  - "go/internal/config/config.go"
  - "go/internal/state/keys.go"
  - "go/internal/control/{worker,worker_test,identity,encryption_test,join_test}.go"
  - "go/internal/transport/{livekit,egress,egress_test}.go"
  - "python/dafter_runtime/src/dafter_runtime/control.py"
  - "python/dafter_runtime/tests/test_control.py"
---

# Rules enforced in code: keys, tokens, participants

Identical on both halves (Go and Python) unless stated. Index:
`.agents/skills/project-context/SKILL.md`.

## Encryption keys

- `privacyMode` decides encryption: resolution stamps `media.encryption` into the hashed document.
- Under `e2ee` the control plane mints one shared key per session and returns it as `encryptionKey`
  beside the token, never inside the resolved document.
- Who receives it derives from the role and the mode, never from the request: `DisclosesKeyTo` in
  `go/internal/config/config.go`.
- The agent worker never gets it through the media server (dispatch or job metadata, attributes,
  data).
  - It calls `POST /sessions/{id}/agent/key` at job start with the worker credential
    `DAFTER_WORKER_SECRET` and the job's `configHash` (`go/internal/control/worker.go`,
    `python/dafter_runtime/src/dafter_runtime/control.py`).
  - It joins with the browser's key provider settings: raw 32-byte key, HKDF, ratchet window 0,
    failure tolerance -1.
  - The scribe calls `POST /sessions/{id}/scribe/key` the same way (`ControlPlane(role="scribe")`).
- At rest the key is sealed with AES-GCM under `DAFTER_STATE_KEY` (`go/internal/state/keys.go`), the
  session id as associated data.

## Tokens and participants

- Token grants derive from the role, never from a client request. No role is issued a room-admin,
  room-create, room-list, room-record or ingress grant: `grantsFor` in
  `go/internal/transport/livekit.go`.
- State every permission explicitly; the media server grants an absent permission by default.
- Only the per-call service token in `go/internal/transport/egress.go` carries `roomRecord`,
  `roomCreate`, `roomList` or `sip.call`. It is a separate claims type, never returned to a client.
- A joiner's participant id is minted by the control plane or, when the join names a device key,
  derived from the session and that key under a server key (`participantFor` in
  `go/internal/control/identity.go`). A rejoin or a second tab replaces its own connection.
- Display names travel only on the `dafter.name` data topic, never in a token.
