# Webhook fixtures

What the media server posts to the control plane's `/livekit/webhook`, read
by `go/internal/transport/webhook_test.go`. The body is a `WebhookEvent`
(`livekit_webhook.proto` in `livekit/protocol`) as protojson: enum names, int64
as decimal strings, and zero values left out, so an audio track carries no
`type` at all (`TrackType` AUDIO is 0). The adapter reads only the event name,
the room name and the track sid, and asks the room service who publishes the
track rather than trusting the body for it.

The request carries the body's SHA-256, base64, in the `sha256` claim of an
HS256 token in `Authorization`, issued under the API key and signed with its
secret (`webhook.URLNotifier` in `livekit/protocol`).

- `track-published.json`: a browser participant's microphone published
- `participant-joined.json`: a person joined (kind STANDARD, so absent);
  `participant-joined-agent.json` is the agent worker joining (kind AGENT).
  The control plane tells them apart by identity: only a participant id it
  minted (`p_`) is a person.
- `egress-ended.json`: a recording finished; `egressInfo` is an `EgressInfo`
  with `endedAt` in nanoseconds.
- `room-finished.json`: the media server closed the room, which it does
  `departureTimeout` seconds after the last participant other than an agent or
  a recorder left (observed: a room holding only the agent and two egress
  recorders closed 20 s after the last person left).

The shapes were captured from `livekit/livekit-server` on the dev stack and
trimmed; the room's `turnPassword` and every egress request (which carries
storage credentials) are left out.
