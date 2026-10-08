---
paths:
  - "go/cmd/dafter-control/*.{js,css}"
  - "go/cmd/dafter-control/testclient.html"
  - "go/cmd/dafter-control/jstest/**"
  - "go/cmd/dafter-control/{main,assets}.go"
---

# Test client

## Test client

- The main screen holds only the call: language, LLM, the fillers, backchannel and normalization
  toggles (`SPEECH_TOGGLES` in `agent-speech.js`, sent as session overrides of the existing config
  fields), start, media, leave. Every other control lives under the one closed Advanced section,
  pinned by `jstest/layout.test.mjs`.
- Agent UI (selector, agent tile, invite/remove, live transcript, per-turn latency by layer beside
  the latency heard in the browser, running cost, refusals): `go/cmd/dafter-control/agent.js`,
  `agent-call.js`, `agent-turns.js`, `agent-metrics.js`, `agent-refusal.js`, `agent-addressing.js`
  (dormant or awake, the Wake button) and `agent.css`, served from `clientAssets` in `main.go`.
- Transcription badge, Captions panel, tile captions: `client-captions.js`, `captions.css`.
- Scribe Notes panel, judge score, minutes on Leave: `client-scribe.js`, `scribe.css`.
- Rest of the client: `client.js` (SDK setup, e2ee, noise filters, media options),
  `client-stats.js`, `client-session.js`, `client-call.js`, `client-media.js`, `client-captions.js`.
  Classic scripts sharing globals, loaded in that order after the agent files.
- New client code goes in files like these, not in `testclient.html`. A new file needs a
  `//go:embed` line and a `clientAssetPaths` entry.
- In an e2ee room the worker's SDK also encrypts its data packets (`dafter.events`,
  `lk.transcription`). The pinned `livekit-client` must be 2.16 or newer, with the key provider in
  `RoomOptions.encryption`, not the deprecated `e2ee`. An older client plays the agent's audio but
  silently drops its state and transcript.
