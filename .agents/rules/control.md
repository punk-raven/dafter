---
paths:
  - "go/internal/control/{control,control_test,agent,agent_test,scribe,scribe_test}.go"
  - "go/internal/control/{lifecycle_test,locks,release_test}.go"
  - "go/internal/control/{recording,recording_test,recording_state_test,webhook,webhook_test}.go"
  - "go/internal/control/{metrics,metrics_internal_test}.go"
  - "go/internal/control/{canary,canary_test,canary_internal_test}.go"
  - "go/internal/transport/{dispatch,dispatch_test,egress,egress_test,webhook,webhook_test}.go"
  - "go/internal/transport/{rooms,rooms_test}.go"
  - "go/internal/transport/testdata/{dispatch,egress,webhook}/**"
  - "go/internal/state/{state,lifecycle,state_test,recordings_test}.go"
  - "python/dafter_runtime/src/dafter_runtime/plan.py"
  - "python/dafter_runtime/tests/test_plan.py"
  - "testdata/agent/**"
  - "go/cmd/dafter-control/{client,voice-gate}.js"
  - "go/cmd/dafter-control/jstest/{noise,voicegate}.test.mjs"
---

# Rules enforced in code: control plane

Identical on both halves (Go and Python) unless stated. Index:
`.agents/skills/project-context/SKILL.md`.

## Sessions, dispatch and the agent

- Write a session to the store before minting its token.
- An agent session is dispatched after it is stored and before any token is minted (`dispatchAgent`
  in `go/internal/control/control.go`).
- A scribe session gets a second dispatch to `scribe.pool` with the same document right after
  (`dispatchScribe`, `scribe.go`); a failed one is logged and never fails the call.
- The job metadata is the stored, hashed document byte for byte. The worker validates it, re-hashes
  it and refuses a mismatch (`load` in `python/dafter_runtime/src/dafter_runtime/plan.py`). It calls
  the control plane only at job start (session key, refusal report), never mid-turn.
- `CreateDispatch` needs `roomAdmin` on the one room, carried only by the per-call service token in
  `go/internal/transport/dispatch.go`; request JSON pinned in
  `go/internal/transport/testdata/dispatch/`.
- `testdata/agent/` pins one job per focus language (hi, en-IN, kn-IN, mr-IN, te-IN), read by both
  halves.
- `POST /sessions/{id}/agent/start|stop` (`go/internal/control/agent.go`) brings the agent into or
  out of a running session. The stored config is the authority:
  - Start is refused when the stored document has the agent off; both are refused for a sealed
    session; the body takes no fields.
  - Start first recalls the pool's existing dispatches, so the room holds one agent.
  - `RecallAgents` in `go/internal/transport/dispatch.go` deletes only dispatches of the session's
    pool (every room also carries a default dispatch with an empty agent name) and skips a closed
    room, found with a `roomList`-only service token (listing a closed room's dispatches waits on a
    node that hosts nothing).

## Versions and canary

- A session carries the bundle it runs as `version` (`id`, `candidate`) in its hashed document;
  the worker labels every metric with it, the control plane counts
  `dafter_sessions_by_version_total{version,arm}`. Publish a changed bundle under a new id.
- `routeCanary` (`go/internal/control/canary.go`), in `openSession` before resolution: the
  session's profile's `canary.percent` of sessions go to `canary.profile`, by SHA-256 of the
  candidate's version id and the device key or inbound caller number, else the session id. Default
  0 (off); 5 to 10 for a first rollout. Never log or store the caller key.
- Promote: point the agent, trunk or sessions at the candidate profile; roll back: percent 0. Both
  through the admin API, no redeploy. Raise percent above 0 only after the candidate passes the
  `dafter-scenarios` pass^k gate in CI.

## Recording and webhooks

- The control plane starts and stops recordings from the session's stored config
  (`POST /sessions/{id}/recording/start|stop`, `go/internal/control/recording.go`) over the media
  server's Twirp JSON API, no vendor SDK. Request JSON pinned in
  `go/internal/transport/testdata/egress/`; the server answers with proto field names (`egress_id`).
- The media server posts signed webhooks to `/livekit/webhook` (`go/internal/transport/webhook.go`),
  handled off the request in `go/internal/control/webhook.go`:
  - `track_published` for an audio track: starts that voice's own track egress when
    `recording.tracks` is on.
  - `participant_joined` by a `p_` id: marks the session joined.
  - `egress_ended`: settles a recording.
  - `room_finished` (sent `departure_timeout` seconds after the last person leaves; agents and
    recorders do not count): ends a joined session, stops its recordings and closes its dial-in. An
    ended session refuses joins with `session_ended` (410).
- Recording state is serialized per session, never globally.
- The binary serves `MetricsHandler`, not `Handler`: register every route in
  `go/internal/control/metrics.go` too. The control tests drive that mux.

## Client noise filters

- The publishing client applies the session's noise filter from one registry, `NOISE_FILTERS` in
  `go/cmd/dafter-control/client.js`. A member of `noiseCancellation` without an entry there does
  nothing.
- Never run the browser's own suppression and a processor together (stacked suppressors degrade
  speech). Echo cancellation is separate and stays on.
- The worker's own filter (`agent.pipeline.noiseFilter`, `agent.md`) never stacks on a client
  processor: while it is not `off` the worker refuses `rnnoise` and `rnnoise_gated` before joining;
  `off` and `native` run beside it.
- Attach a processor to the microphone track before it is published, never after.
- Worklet and WASM assets come from a pinned CDN URL beside the SDK pin. Every load failure falls
  back to `native` visibly (the call shows the active filter).
- `rnnoise_gated` adds the near-voice gate (`voice-gate.js`, served by the control plane) and needs
  automatic gain off (AGC lifts the room to the caller's level when they are quiet).
