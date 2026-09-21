# Media profile - plan and status

Scope: the real-time WebRTC media path - what a client publishes, what the SFU
routes, what egress encodes. The agent runtime is out of scope.

Stages 0-3 are built. Stage 4 is Phase 2 work, stage 5 is deferred by decision,
stage 6 is the standing deferred list.

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
operations are exactly what decision 9 admits.

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
| Scope | Stages 0-3 only. Egress encoding and room encryption stay filed |
| Default codec | **VP9 primary, H.264 backup.** Most of the compression gain at broader hardware support than AV1 |
| `sealed` privacy mode | Left exactly as it is for the POC and revisited in Phase 2. One tenant, no external consumer, nobody to mislead yet |
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

### Stage 4 - egress encoding (Phase 2)

Server-side, fully Dafter-owned, and absent in both halves.

1. Build egress orchestration in Go, inside `go/internal/transport` - depguard
   denies `github.com/livekit/*` everywhere else, and that is the correct home.
   Today the only thing that starts an egress is `lk` in
   `scripts/measure-egress.sh`.
2. Set encoding options from resolved config on each egress request:
   resolution, video bitrate, framerate, codec.
3. Vary them by layout and `retentionClass` - an evidence-grade room composite
   and a per-track audio archive do not want the same encode.

**Deliverable:** a recording whose encode settings came from the session's own
stored config, recorded in its manifest.

### Stage 5 - room encryption (deferred to Phase 2)

`privacyMode` is policy with no mechanism: `sealed` is enforced by refusing an
agent, and no LiveKit encryption is wired into the room. That is acceptable
while the POC has one tenant and no external consumer, which is why this is
deferred rather than reserved or built.

**What makes it urgent again:** the first consumer who can select `sealed`. At
that point the mode claims a guarantee nothing enforces, and the choice is build
the mechanism or refuse the mode at the API - not leave it.

Three consequences to accept in writing first, because end-to-end encryption
removes capabilities rather than adding a flag:

- the SFU sees ciphertext, so server-side egress cannot record or transcode -
  `sealed` means client-side recording or none, which `docs/dafter.md` already
  states and `rules.go` does not yet enforce
- encryption is enabled per participant in the SDK, so a participant who does
  not enable it is not merely insecure, they are unintelligible
- key distribution and rotation become Dafter's problem, shared-key or
  per-participant, and that decision is the real work here

### Stage 6 - deferred, with reasons

| Question | Why it waits |
|---|---|
| Krisp browser noise filter | Closed, not open. Enhanced Krisp and background-voice cancellation are LiveKit Cloud only and the reference topology is self-hosted OSS. The `noiseCancellation` field keeps the door open at no cost; the license path is a procurement question nobody has asked |
| Hi-fi audio (up to 510 kbps stereo) | No known consumer. Voice defaults suffice until one exists |
| Ingress (OBS, external stream import) | Nothing in the delivery plan asks for it |
| Raw-track processing, frame metadata | Client-SDK capability, Phase 3 at the earliest |

## 6. Where this lands in the delivery plan

Stages 0-3 are POC work and close the "video call" pass bar once the uplink
number exists. Stage 4 ships with recording orchestration in Phase 2. Stage 5 is
deferred there by decision. Stage 6 is Phase 7 or never.

Nothing here is on the agent runtime's critical path.
