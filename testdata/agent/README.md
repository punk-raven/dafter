# Agent job vectors

Each file is the exact job metadata the control plane hands the agent worker
for a session with no profile: the resolved session config for session
`s_7f3a9c21`, sealed with its own `configHash`, byte for byte as the embedded
catalog resolves it.

- `hindi-webrtc-job.json`: Hindi, no overrides.
- `hindi-semantic-webrtc-job.json`: Hindi with the session override
  `{"turn": {"strategy": "semantic", "localVadEnabled": true}}`, the A/B that
  selects the on-device turn detector.
- `hindi-groq-webrtc-job.json`: Hindi with the session request's `llm` naming
  the route `groq/qwen/qwen3.8-27b`, so the LLM stage is Groq and the rest
  stays Sarvam.
- `english-webrtc-job.json`: `en-IN`, no overrides.
- `kannada-webrtc-job.json`, `marathi-webrtc-job.json`,
  `telugu-webrtc-job.json`: `kn-IN`, `mr-IN` and `te-IN`, no overrides.
- `hindi-telephony-job.json`: Hindi on the telephony channel with the session
  override `{"recording": {"enabled": true, "layout": "room_composite",
  "startAt": "session_create", "consentArtifactId": "consent_call"}}`, a
  recorded phone call: the channel answers every turn, greets, and pins STT
  and TTS to the 8 kHz line.

- `go/internal/control/agent_test.go` resolves the catalog and fails if the
  bytes differ. Rerun with `DAFTER_UPDATE_FIXTURES=1` when the catalog changes
  on purpose, then run the Python tests too.
- `python/dafter_runtime/tests/test_plan.py` loads the Hindi job the way the
  worker loads a job: validate, re-hash, then plan the pipeline, and expects
  the Sarvam cascade with provider endpointing and a local VAD that only
  catches barge-in. `test_llm_routes.py` plans the Groq job and builds every
  catalog route. `test_turn_detection.py` plans the semantic Hindi and the
  English jobs and expects the turn detector. `test_languages.py` reads every
  focus language's job and the catalog itself: each language hears and speaks
  its own language code with its own voice, and semantic turn detection is
  chosen only for a language the on-device detector covers. `test_telephony.py`
  plans the phone call.

A catalog change that the worker cannot run fails on the Python side instead
of at the first real call.
