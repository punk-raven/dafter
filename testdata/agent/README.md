# Agent job vectors

Each file is the exact job metadata the control plane hands the agent worker
for a WebRTC session with no profile: the resolved session config for session
`s_7f3a9c21`, sealed with its own `configHash`, byte for byte as the embedded
catalog resolves it.

- `hindi-webrtc-job.json`: Hindi, no overrides.
- `hindi-semantic-webrtc-job.json`: Hindi with the session override
  `{"turn": {"strategy": "semantic", "localVadEnabled": true}}`, the A/B that
  selects the on-device turn detector.
- `english-webrtc-job.json`: `en-IN`, no overrides.

- `go/internal/control/agent_test.go` resolves the catalog and fails if the
  bytes differ. Rerun with `DAFTER_UPDATE_FIXTURES=1` when the catalog changes
  on purpose, then run the Python tests too.
- `python/dafter_runtime/tests/test_plan.py` loads the Hindi job the way the
  worker loads a job: validate, re-hash, then plan the pipeline, and expects
  the Sarvam cascade with provider endpointing and a local VAD that only
  catches barge-in. `test_turn_detection.py` plans the other two and expects
  the turn detector.

A catalog change that the worker cannot run fails on the Python side instead
of at the first real call.
