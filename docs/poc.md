# Dafter POC

Prove the hard parts work before committing to Phase 1. Throwaway code: one region, one tenant, one provider per stage, secrets in env vars. Everything runs on docker compose, sized for 1-3 users.

## How the pieces connect

Control plane (Go, SQLite) resolves config, mints a LiveKit token, dispatches the agent worker, returns a join URL. The browser and agent worker both join LiveKit with their tokens. The worker runs VAD -> STT -> LLM -> TTS on the user's audio and publishes speech back. Egress writes recordings to MinIO. The resolved config document is the contract between Go and Python - resolved once, hashed (RFC 8785), handed to the worker. The worker calls back only at job start: for the session key when the room is end-to-end encrypted, and to report a job it refuses. It never calls back mid-turn.

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

**Recorded, Hindi only (2026-09-24, one session each, `dafter-evals` against a local SFU, worker in Hyderabad, ~40ms RTT to Sarvam).** Gap is end of the user's synthesized speech leaving the client to the first agent audio arriving back, so it includes WebRTC both ways but no device output.

| Measure | Catalog (silence 500ms, min words 2) | Pass |
|---|---|---|
| Turn gap, 20 turns | p50 2002ms, p95 2187ms, stdev 154ms | Fail (p95 under the 2.5s redesign line) |
| of which end of speech to `thinking` | p50 932ms | |
| of which `thinking` to first audio | p50 1052ms (trace: LLM TTFT p50 367ms, TTS TTFB p50 287ms) | |
| Barge-in, 5 trials | 4 stopped, p50 1208ms, max 1448ms | Fail |
| Backchannel / filler, 5 each | 0 interrupted; 1 of each answered as a new turn | Pass on interruption |

A silence window of 300ms moved the gap p50 to 1728ms (one Sarvam final arrived 8s late). `minWords: 0` brought barge-in p50 to 507ms but let 3 of 5 backchannels interrupt. With provider endpointing and no local VAD, interruption waits for transcribed words, so the 300ms barge-in bar needs a local VAD or a faster onset signal: that is the design question this stage leaves open. English (Deepgram, Silero, semantic turn detection) is not built; the worker refuses those jobs.

For a real-time check, the test client shows the agent as a call participant: invite or remove it mid-call, a live transcript, and per turn the end of speech to `thinking` (endpoint), `thinking` to first agent audio heard in the page (respond), their sum, and a running p50, measured in the browser from the microphone and agent audio levels. One headless session with a synthesized Hindi question and a spoken interruption (2026-09-24) read totals of 2620ms and 1880ms, endpoints of 893ms and 883ms, and a barge-in stop of 1326ms, in line with the table above.

**The agent in an end-to-end session (`trusted_agent`, 2026-09-24).** The worker fetches the session's shared key from the control plane at job start, over its own credential and never through the media server, and joins with the same key provider settings as the browser. One headless session with a synthesized Hindi question: the agent joined as an encrypted participant, its greeting and reply decoded in the browser with no concealed samples, and it transcribed the question, so both directions decrypt. `open` still dispatches and greets unencrypted; `sealed` still refuses an agent at the API.

What frame encryption in that session covers and what it does not:

| Covered (the media server relays ciphertext) | Not covered (the media server or Dafter still sees it) |
|---|---|
| Audio and video frames both ways, except the first byte of each audio frame (Opus TOC) and a few header bytes of each video frame, left clear by the frame cryptor | The session key itself: minted, stored and disclosed by the control plane, so this protects against the media server and the network, not against Dafter |
| The agent's data packets, including `agent.state_changed` on `dafter.events` and its transcripts on `lk.transcription`: its SDK encrypts them in an e2ee room, which is why the test client needs `livekit-client` 2.16 or newer | Participant identities, names, attributes (`dafter.role`), join and leave times, track and room names, the room's job metadata (the resolved document) |
| The browser's own data packets, since it passes the key provider as `RoomOptions.encryption` | Packet sizes and timing, and the per-packet audio level header the media server uses for active speaker detection, so who speaks when is visible |
| | The key on the worker-to-control call travels over plain HTTP in the dev stack; outside it that call needs TLS. The worker credential is one static secret for the whole pool, and the `configHash` it sends is in the job metadata the media server sees, so the credential is the only gate |

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
