# Agent job vector

`hindi-webrtc-job.json` is the exact job metadata the control plane hands the
agent worker for a Hindi WebRTC session with no profile or overrides: the
resolved session config for session `s_7f3a9c21`, sealed with its own
`configHash`, byte for byte as the embedded catalog resolves it.

- `go/internal/control/control_test.go` resolves the catalog and fails if the
  bytes differ. Rerun with `DAFTER_UPDATE_FIXTURES=1` when the catalog changes
  on purpose, then run the Python tests too.
- `python/dafter_runtime/tests/test_plan.py` loads it the way the worker loads
  a job: validate, re-hash, then plan the pipeline, and expects the Sarvam
  cascade with provider endpointing and no local VAD.

A catalog change that the worker cannot run fails on the Python side instead
of at the first real call.
