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
