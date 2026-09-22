# Media profile - plan and status

Scope: the real-time WebRTC media path - what a client publishes, what the SFU
routes, what egress encodes. The agent runtime is out of scope.

Stages 0-5 are built. Stage 6 is the standing deferred list.

## 1. The gap this closed

Dafter surfaced no media controls beyond token role grants. The transport seam
is 24 lines and its whole surface is `MintToken(Grant)`. Nothing in `schemas/`,
`go/`, `python/` or `deploy/` named a codec, a bitrate, a resolution, simulcast,
dynacast, adaptive stream, RED, DTX, noise cancellation, or room encryption. A
grep for all of them returned two hits, both prose in a schema description.

Every bandwidth-versus-quality lever therefore ran at whatever the client
library defaulted to, and those defaults are not neutral. Read from the pinned
`livekit-client@2.13.5` bundle: the publish codec is VP8, and both `dynacast`
and `adaptiveStream` are off. The platform was publishing with the least
efficient codec and neither of the two levers that save uplink.

**The stance.** Almost every lever is applied by the publishing client, and the
SFU only routes what it is handed. That does not make it the client's problem.
Dafter ships an opinionated profile as resolved config, and a client overrides
it only when it has a reason to. An unset knob is a decision nobody made.

**Why config and not a client.** The resolved document is already hashed and
stored with the session. The profile in it means the recording manifest can
answer "what was this published at" a year later, from the same record that
answers "which model spoke". In client code, that question has no answer.

## 2. What the seam did not have to become

Decision 9 keeps the SFU behind a deliberately narrow, control-side seam. None
of stages 0-3 widened it.

`POST /sessions` already returned the full resolved document to the caller -
`createSessionResponse.Config`, beside the token - and `POST /sessions/{id}/join`
returns the stored one. The media profile is a field in a document the client
already receives. Delivery needed no token `RoomConfiguration`, no new endpoint,
and no new method on `Transport`.

Egress is the one part that adds a control-side operation, and control-side
operations are exactly what decision 9 admits. Stage 4 widened `Transport` by
exactly that: `StartEgress` and `StopEgress`, driven over the media server's
own Twirp API with no vendor SDK, and nothing else.

## 3. Corrections to the gap analysis this came from

| Claim | Correction |
|---|---|
| Deliver the profile via token `RoomConfiguration` | Unnecessary. The session-create and join responses already carry the resolved document |
| Egress `EncodingOptions` belong in `deploy/egress.yaml` | They do not. Encoding is a per-request field on the egress API, so it cannot be set until Go orchestration exists. The dependency runs the other way |
| Resolution preset not set | The test client already captured at `h720`. Everything else about it was untuned |
| Docs conflate sealed with recording-at-rest | They do not. `docs/dafter.md` separates transport, end-to-end and at-rest encryption, and the privacy-mode table already says `sealed` means full end-to-end. The gap is in the code, not the document |
| Simulcast "default on", so nothing to do | True but incomplete. Simulcast is on by default, and a layered codec supersedes it for its own stream. It still governs the non-layered backup path, which is why the profile states it |

Two further facts the analysis did not reach, both since fixed:

- `long_form` was in the `Channel` enum with no overlay in `catalog.json`, so
  any request naming it failed resolution on the channel axis. A member of a
  closed enum that could not be used.
- Telephony carries narrowband audio with no video. A video profile resolved on
  `channel: telephony` is a contradiction the schema cannot express, which makes
  it a cross-field rule - the same shape as "sealed forbids agent".

## 4. Decisions taken

| Question | Decision |
|---|---|
| Scope | **Widened twice.** Stages 0-3, then stage 4 (egress encoding) and stage 5 (room encryption). Both were filed as later work and both were built; the rows below say why each was reversed |
| Default codec | **VP9 primary, H.264 backup.** Most of the compression gain at broader hardware support than AV1 |
| `sealed` privacy mode | **Reversed.** Built in stage 5 rather than deferred: a mode that names a guarantee and enforces none is the one thing worse than not offering it |
| E2EE key model | **One random 256-bit key per session, minted and held by the control plane.** It proves encryption against the media server and the network, not against Dafter. The consumer-held key is Phase 2 |
| Noise cancellation | The `noiseCancellation` field ships so the shape exists; WebRTC native is the only implementation. No Krisp |

## 5. Stages

### Stage 0 - fix the numbers (done)

Pinned to `livekit-client@2.13.5`, read from the published bundle rather than
from documentation about it.

| Preset | Size | Max bitrate | Max framerate |
|---|---|---|---|
| `h180` | 320x180 | 160 kbps | 20 |
| `h360` | 640x360 | 450 kbps | 20 |
| `h540` | 960x540 | 800 kbps | 25 |
| `h720` | 1280x720 | 1.7 Mbps | 30 |
| `h1080` | 1920x1080 | 3.0 Mbps | 30 |

Library defaults worth knowing, because they are what the platform was getting:
`videoCodec` VP8, `adaptiveStream` false, `dynacast` false, `simulcast` true,
`dtx` true, `red` true, `backupCodec` true - which resolves to VP8, not H.264.
A scalability mode is parsed as `L<spatial>T<temporal>` with an optional
key-frame suffix and defaults to `L3T3_KEY` once a layered codec is in use.

The codec was already chosen, so what remains of this stage is validation
rather than selection: VP9 encode cost and H.264 fallback behaviour on the
devices the POC targets. A device that cannot encode VP9 at the profile's
bitrate is the finding worth having.

### Stage 1 - the `media` block (done)

`$defs/Media` on the resolved-session-config schema, with `VideoProfile` and
`AudioProfile` beside it, all closed with `unevaluatedProperties: false`.

Three enums generate to both halves through `go/tools/enumgen` with drift
coverage on each side. Scalability mode stays a pattern: its grammar is open
enough that the client library parses it with a regex rather than publishing a
closed set, and inventing one here would be a set that drifts.

`noiseCancellation` is `off | native` only. A vendor filter would be a third
member with nothing implementing it, which is the same failure as a privacy
mode that promises encryption and checks a flag.

Video and audio fields are pointers in Go and optional in Python, because a
profile is the product of merged layers where a `false` a layer set cannot be
told from an unset field. `enabled` is stated rather than omitted for the same
reason: a merge can override a key but cannot delete one, so a channel without
video has to say so.

### Stage 2 - defaults and overlays (done)

The default profile is VP9 with an H.264 backup, `L3T3_KEY`, 720p at 1.7 Mbps
and 30 fps, simulcast, dynacast and adaptive stream on, RED and DTX on, echo
cancellation and native noise suppression stated.

Per channel: `webrtc` leaves the defaults alone, because the defaults are the
webrtc profile. `telephony` states that video is off. `long_form` gets an
overlay for the first time, trading resolution for a bitrate a long session can
hold, at the 540p preset triple rather than an invented one.

Two cross-field rules, one table per half, each located by JSON pointer: video
on telephony, and a scalability mode without a layered codec.

**Delegating the size.** `resolution` also accepts `auto`, which hands that one
choice to the publishing client - often the only party that knows what its
camera and its hardware encoder do well, and a laptop, a phone and a kiosk on
one tenant do not agree. It is a stated value rather than an absent one, and
the difference is the whole point: an absent field is a decision nobody made,
while `auto` is a decision to delegate and lands in the hashed document like
any other, so a session that let the client choose still says so afterwards.

The bitrate and framerate ceilings still apply on top of it. Delegating the
size never delegates the bandwidth, so a client that picks a size its ceiling
cannot carry gets a soft picture rather than a bigger bill. Both halves pin
that, because the tempting reading of `auto` is that it switches everything
off. The shipped default stays `h720`; opting out has to be asked for, by a
tenant, a profile, a channel or a single request.

### Stage 3 - make it real on the wire (done)

The test client reads `config.media` off the session response and maps it onto
room options: codec and backup codec, scalability mode, simulcast, the bitrate
and framerate ceiling, capture resolution, RED and DTX, and explicit echo
cancellation and noise suppression. A field the profile omits is omitted there
too, so the library default stands rather than being overwritten by a guess.

Native echo and noise suppression stop being browser defaults here and become a
stated choice, which by the recorded decision is the whole of the
noise-cancellation work.

The permanent home is `client-core` in Phase 3. The test client proves the
profile survives the trip in the meantime.

**What is not yet measured.** Publisher uplink before and after, through
`make loadtest-media`, same room and same participants. The profile is applied
and verified against a running control plane, but the bandwidth claim is not
evidence until that number exists. A profile that does not move it is not doing
anything.

### Stage 4 - egress encoding (done)

Server-side and fully Dafter-owned. The control plane starts and stops
recordings from the session's stored config, and the encode settings a
recording got are in the same hashed document that says what the room
published.

**The profile.** `media.egress`, beside the publish profile: `width`,
`height`, `framerate`, `videoBitrate` and `audioBitrate` in kbps (the unit the
egress API takes, deliberately not the bps of `maxBitrate`), `videoCodec`, or
a `preset` naming one of the media server's own. Two enums generate to both
halves with drift coverage on each side.

The codec enum is H.264 in its three profiles and nothing else. This was
verified against the egress service's source rather than its protocol file:
the protocol enum also names VP8, which `applyAdvanced` accepts and then
ignores, and VP9 and AV1 are not in it at all. A member the file could not
actually be encoded with would be a setting the manifest records and the
recording does not have. The publish codec is a separate choice, and VP9 in
the room with H.264 in the file is the normal case.

**The default.** 1280x720 at 30 fps, 3000 kbps H.264 main, 128 kbps audio.
This is the media server's own `H264_720P_30` preset spelled out, so a reader
of the document sees the size and rate rather than a name to look up. 720p
matches the publish profile's `h720`, so the composite renders tiles at the
size they arrive rather than upscaling; 3000 kbps sits comfortably above the
1.7 Mbps the room publishes at, so the re-encode is not the quality floor;
H.264 main is what every player decodes. Telephony gets 64 kbps audio and no
video fields at all.

The video encode lives on the channel overlays rather than in `defaults`.
Layers merge and a merge cannot delete a key, so a video encode in
`defaults` would reach telephony and break the audio-only rule below.
Whether a recording has a picture is a channel property anyway.

**Layout rules.**

| Layout | Request | Encode |
|---|---|---|
| `room_composite` | `StartRoomCompositeEgress` | from `media.egress`; `audio_only` when the session publishes no video; MP4, or the container the service picks for audio-only |
| `track_composite` | `StartTrackCompositeEgress` | the same; the client supplies the track ids, being the only party that knows them |
| `track` | `StartTrackEgress` | **none**. The service copies the published bytes and there is nothing to set, so the profile is not consulted and the request carries no encoding even when the document has one |

Files land at `{sessionId}/{layout}-{utc}` (`track-{trackId}-{utc}` for a
track), so one session's recordings share a prefix and each object says its
layout. `retentionClass` is not varied: it is a free-form pattern with no
defined members, and giving an undefined class an encode would be inventing
semantics. The hook is the channel overlay; a class-keyed axis can be added
to the catalog when the classes exist.

**Cross-field rules**, one table entry per half, each located by pointer:

- a preset beside explicit fields is refused at `/media/egress/preset`. The
  API takes one or the other, and a document carrying both has given two
  answers to one question. Because the merge cannot delete a key and the
  shipped catalog states explicit fields, a preset is only reachable from a
  catalog built the other way round; the field exists so the shape is there
- video encode settings on a recording whose session publishes no video are
  refused at `/media/egress`, but only when recording is enabled. An inert
  profile on a session that records nothing is not an error

**The endpoints.** `POST /sessions/{id}/recording/start` and `/stop`, and
`GET /sessions/{id}` to read a session with its recordings (egress id, layout,
start and stop times). The stored document decides everything at every call,
not only at resolution: a session that resolved without recording is refused
at `/recording/enabled`, a missing consent artifact at
`/recording/consentArtifactId`. `startAt: session_create` is honored in the
create path: the room is created and the composite started after the session
is stored and before its first token is minted, so nobody can publish into an
unrecorded room, and a start the server refuses fails the create before a
token exists.

The media server refuses an egress on a room nobody has joined, which is why
the create path creates the room first; an on-demand start does not, so a
start on a room that has since closed is refused rather than quietly
recording an empty one. The service token for these calls carries
`roomRecord` or `roomCreate`, one grant per call, lives for a minute and is
never returned to anything. Participant grants are untouched.

Egress storage is `DAFTER_EGRESS_S3_*` in the environment, set in
`docker-compose.yml` to match `deploy/egress.yaml`. `scripts/measure-egress.sh`
still drives `lk` directly, because it measures both layouts against one room
and a session's layout is fixed in its config; it stays for the gap number
and the endpoint supersedes it for everything else.

**What was verified**, on the dev stack (livekit-server v1.13.7, egress
v1.14.1, MinIO), with the test client publishing a synthetic pattern from
headless Chrome and the recording started and stopped from its own buttons:

| Layout | `ListEgress` while running | File in MinIO | `ffprobe` |
|---|---|---|---|
| `room_composite` | `EGRESS_ACTIVE`, `advanced: {width: 1280, height: 720, framerate: 30, audio_bitrate: 128, video_codec: H264_MAIN, video_bitrate: 3000}` | `s_9f1facd2/room_composite-20260922103213509.mp4`, 4.8 MB, 24.9 s | h264 Main 1280x720 30/1, aac 124 kbps |
| `track_composite` | `EGRESS_ACTIVE`, same `advanced`, both track ids | `s_4b4b7c88/track_composite-20260922103326958.mp4`, 3.8 MB, 19.6 s | h264 Main 1280x720 30/1, aac 124 kbps |
| `track` (audio) | `EGRESS_ACTIVE`, no encoding in the request | `.../track-TR_AMvyF8icsgpiJ9-20260922103425192.ogg`, 182 KB, 11.4 s | opus 48 kHz, ogg: the published bytes |
| `room_composite`, `startAt: session_create` | started at create, 16 s before the first join | `s_ed98ef45/room_composite-20260922103706140.mp4`, 13.8 s | h264 Main 1280x720 30/1 |

The measured video bitrate came out at 1.4 Mbps against the 3000 kbps target,
which is the encoder not needing its budget for colour bars; the target is a
ceiling, not a constant rate.

The last row is the capture-start gap made visible: the egress was accepted
at session creation, but the room composite's own pipeline waits for the
first published track, so the file covers the 14 s after the join and not
the 16 s before it. The guarantee `docs/dafter.md` gives is that this gap is
a measured number in the manifest, not that it is zero; the manifest is the
seal stage's work.

**Not in scope here:** the seal stage, the manifest, retention enforcement,
consent capture, and the recording manifest entry that would carry the encode
settings alongside the plaintext hash. The document already holds them; the
manifest copies them out.

**What stage 5 takes away from this.** None of it runs on an end-to-end
encrypted session. The egress reads the same ciphertext the SFU does, so a
`sealed` or `trusted_agent` session is refused `recording.enabled` outright at
`/recording/enabled` rather than recording something unplayable. Every encode
setting above applies to `open` sessions, which is every session that can be
recorded at all.

### Stage 5 - room encryption (done)

`privacyMode` was policy with no mechanism: `sealed` was enforced by refusing an
agent, and no encryption was wired into the room. It is now the mode that
decides how the media is encrypted, stated in the hashed document and applied
on the wire.

**`media.encryption`** is a closed profile with `mode` (`transport | e2ee`) and
`keyModel` (`server_shared`). Resolution derives the mode from the privacy mode
- `open` is `transport`, `sealed` and `trusted_agent` are `e2ee` - and stamps it
wherever the layers left it unsaid, so the document states how the session was
encrypted instead of leaving a reader to infer it from the privacy mode a year
later. Only an absent value is filled in: a layer stating a contradicting mode
keeps it and is refused at `/media/encryption/mode`, rather than being silently
corrected. `keyModel` has one member on purpose - the enum exists so
`consumer_held` is a second member later and not a change of shape.

**The key model, and what it does not prove.** One random 256-bit key per
session, minted by the control plane at create when the mode is `e2ee`, stored
with the session so every join is handed the same one, and returned beside the
token as `encryptionKey` - never inside the resolved document, which is hashed,
stored and shown to every joiner. Who receives it derives from the role and the
mode, exactly like a token grant and never from the request: participant,
presenter and observer under either end-to-end mode, the agent only under
`trusted_agent`, a recorder never, and no role at all under `open`.

This proves encryption against the media server and against the network. It does
not prove encryption against Dafter, which mints and holds the key. The
consumer-held key that `docs/dafter.md` promises for `trusted_agent` is a Phase 2
follow-up, and saying so is the point: a key model that is not stated is one a
consumer will assume.

**The three consequences, now accepted rather than anticipated:**

- the SFU sees ciphertext, so server-side egress cannot record or transcode.
  `rules.go` and `rules.py` now refuse `recording.enabled` under either
  end-to-end mode, at `/recording/enabled`, for every layout in the enum -
  they are all server-side. `sealed` means client-side recording or none, and
  that is now enforced rather than documented
- encryption is enabled per participant in the SDK, so a participant who does
  not enable it is unintelligible rather than merely insecure. The test client
  demonstrates it deliberately: a join whose response carried no key builds no
  cryptor, connects anyway, and decodes nobody
- key distribution is Dafter's problem and is answered above. Rotation is not
  answered, and is deferred with the rest below

**On the wire.** The test client constructs the room with
`ExternalE2EEKeyProvider` and the frame cryptor worker from the pinned
`livekit-client@2.13.5`, loads the key and calls `setE2EEEnabled(true)` before
publishing anything, so no plaintext frame is ever sent. It shows
`Room.isE2EEEnabled` in a badge and logs every encryption status change.

**Verified** against the dev stack in headless Chrome, three tabs in one sealed
room: both keyed tabs report `E2EE on` with `isE2EEEnabled` true, `ENCRYPTED`
fires for themselves and for each other, and each decodes every frame it
receives from the other (467/467 and 472/472). The third tab, its key stripped
from the join response, is reported `NOT ENCRYPTED` by the other two, receives
266 frames and decodes 0 while their call continues. An open session carries no
key, builds no `e2ee` room option and shows no badge. `sealed` with recording is
refused at create with `/recording/enabled`.

**Deferred, and why:**

| Question | Why it waits |
|---|---|
| Consumer-held key (`keyModel: consumer_held`) | The mechanism that would make `trusted_agent` mean what `docs/dafter.md` says. It needs a key exchange that never reaches the control plane, which is a Phase 2 design, not a field |
| Key rotation | The shared key lives as long as the session. Rotation needs a ratchet and a rekey trigger, and with no long-lived session and no participant churn to protect against yet, building it now would pin a scheme before there is a requirement |
| Agent-side key use | `trusted_agent` discloses the key to the agent role and no Python worker exists to hold it. The disclosure rule is built and tested; the consumer of it is Phase 5 |
| Recording under E2EE | Client-side recording, or an egress inside the trust boundary. Both are Phase 2 work behind the seal stage |

### Stage 6 - deferred, with reasons

| Question | Why it waits |
|---|---|
| Krisp browser noise filter | Closed, not open. Enhanced Krisp and background-voice cancellation are LiveKit Cloud only and the reference topology is self-hosted OSS. The `noiseCancellation` field keeps the door open at no cost; the license path is a procurement question nobody has asked |
| Hi-fi audio (up to 510 kbps stereo) | No known consumer. Voice defaults suffice until one exists |
| Ingress (OBS, external stream import) | Nothing in the delivery plan asks for it |
| Raw-track processing, frame metadata | Client-SDK capability, Phase 3 at the earliest |

## 6. Where this lands in the delivery plan

Stages 0-3 are POC work and close the "video call" pass bar once the uplink
number exists. Stage 5 lands with them: it is POC work because the mechanism is
small and the alternative was a mode that promised what nothing enforced. Stage
4 is the recording-orchestration half of Phase 2; the seal stage and the
manifest are the rest of it, alongside the consumer-held key and rotation.
Stage 6 is Phase 7 or never.

Nothing here is on the agent runtime's critical path.
