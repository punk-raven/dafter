# Dafter POC

Prove the hard parts work before committing to Phase 1. Throwaway code: one region, one tenant, one provider per stage, secrets in env vars. Everything runs on docker compose, sized for 1-3 users.

## How the pieces connect

Control plane (Go, SQLite) resolves config, mints a LiveKit token, dispatches the agent worker, returns a join URL. The browser and agent worker both join LiveKit with their tokens. The worker runs VAD -> STT -> LLM -> TTS on the user's audio and publishes speech back. Egress writes recordings to MinIO. The resolved config document is the contract between Go and Python - resolved once, hashed (RFC 8785), handed to the worker. The worker never calls back mid-turn.

## 1. Control plane configuration

POST /sessions resolves config through layers (defaults -> tenant -> profile -> overrides) and composition axes (language + channel overlays). Mints a LiveKit token, stores the resolved document with its hash in SQLite.

**Pass:** token joins the room. Bad config rejected at resolution. Same input = same hash. Hindi and English resolve different turn strategies from the same base.

## 2. Video call - TURN, egress, recording

LiveKit SFU in docker. TURN on TLS 443 for mobile/firewall relay. Egress writes to MinIO in both layouts: room composite and per-track.

**Pass:** relay-only call (`iceTransportPolicy: "relay"`) connects on 443. Capture-start gap measured per layout - this number decides the default layout for Phase 1. Files land in MinIO and play.

## 3. Audio call - SIP, telephony, recording (stretch)

SIP trunk routes inbound phone calls into the LiveKit room. Narrowband 8kHz mulaw audio. Included early because telephony forces different turn constants; deferrable if it blocks exit.

**Pass:** real phone call reaches the room. Both sides hear each other. Recording plays back. Cost per call-hour recorded.

## 4. Bots - background and foreground

One bot joining as a participant via the server SDK. Background: audio only, no video track, invisible in composite. Foreground: publishes a video track, appears as a tile.

**Pass:** background bot absent from composite. Foreground bot visible. Both audible in recording.

## 5. Agent - STT, LLM, TTS, VAD, interruptions

Python worker running the cascaded pipeline. Hindi: Sarvam (saaras STT, bulbul TTS, sarvam-105b LLM), local VAD off, provider endpointing, 500ms chunk, raw PCM, prewarmed TTS. English: Deepgram STT, Silero VAD, semantic turn detection. Emits `agent.state_changed` events. 20-turn scripted conversation per language, then barge-in, backchannel ("mm-hmm"), filler ("uh"), and background speech tests.

**Pass:** p50 < 800ms, p95 < 1.5s. Barge-in stops speech within 300ms. Backchannels and fillers do not interrupt. If p95 > 2.5s on any language, pipeline design changes before Phase 1.

## Infrastructure (parallel track)

| Piece | What | How |
|---|---|---|
| SFU | Audio/video routing | LiveKit OSS (docker) |
| TURN | Relay for blocked connections | Cloudflare, TLS 443 |
| DB | Sessions, resolved configs | SQLite |
| Hot state | Locks, presence, events | Redis (docker) |
| Storage | Recordings | MinIO (docker) |
| Tracing | Per-stage latency breakdown | Jaeger (docker) |

`docker-compose.yml` brings it all up. `make poc` from a clean clone reaches a working call.

## Exit

Finished when every pass bar has a recorded number or a "this failed, here is why." One page covers which vendor claims held, cost per call-hour per layout, and a go/no-go on latency.

Not in the POC: multi-tenancy, provider abstraction, recording encryption, consent lifecycle, retention, batch transcript, translation, evals, autoscaling, failover.

## Abbreviations

| Term | Expansion | What it means here |
|---|---|---|
| POC | Proof of concept | This throwaway build, not Phase 1 code |
| SFU | Selective forwarding unit | Media server that routes each participant's tracks to the others without re-encoding; LiveKit is ours |
| ICE | Interactive Connectivity Establishment | How WebRTC peers find a working network path; `iceTransportPolicy: "relay"` forces the TURN path so we can test the worst case |
| TURN | Traversal Using Relays around NAT | Relay used when no direct path exists; ours listens on TLS 443 so corporate firewalls let it through |
| SIP | Session Initiation Protocol | Telephony signaling that bridges inbound phone calls into a room |
| PCM | Pulse-code modulation | Raw uncompressed audio samples; sent to Sarvam STT with no codec in the path |
| mulaw | μ-law companding | 8-bit narrowband telephony audio encoding, 8kHz on the SIP leg |
| VAD | Voice activity detection | Detects speech vs silence; Silero locally for English, provider-side for Hindi |
| STT | Speech to text | Transcription stage of the pipeline; Sarvam saaras for Hindi, Deepgram for English |
| LLM | Large language model | Response generation stage of the pipeline |
| TTS | Text to speech | Speech synthesis stage of the pipeline, prewarmed to cut first-audio latency |
| RFC 8785 | JSON Canonicalization Scheme | Deterministic JSON serialization, so the same config always hashes the same |
| Endpointing | - | Deciding the user has finished speaking and the agent may reply |
| Barge-in | - | User speaks over the agent and the agent stops |
| Backchannel | - | Listener noise like "mm-hmm" that must not count as a turn |
