# Egress request fixtures

The exact JSON the adapter sends to the media server's Twirp egress API, one
file per request type, asserted byte-for-byte by
`go/internal/transport/transport_test.go`. Field names are the protobuf JSON
names from `livekit_egress.proto` and `livekit_models.proto` at
`livekit/protocol` v1.50.1, which is what `livekit/egress` v1.14.1, the dev
stack's image, is built against. A field that is not in those files is not
in these fixtures; a change here is a change to what the service receives
and must be re-read from the proto, not remembered.

- `room-composite.json`: `StartRoomCompositeEgress` with explicit encoding
- `room-composite-preset.json`: the same with a preset
- `room-composite-audio-only.json`: no video, no MP4 container
- `track-composite.json`: `StartTrackCompositeEgress`
- `track.json`: `StartTrackEgress`, which carries no encoding at all
- `stop.json`: `StopEgress`
- `create-room.json`: `RoomService/CreateRoom` (from `livekit_room.proto`),
  sent before a room composite that starts at session creation

Attributing a track recording and reading one back:

- `list-participants.json`: `RoomService/ListParticipants`
  (`ListParticipantsRequest` in `livekit_room.proto`), sent before a track
  egress starts so the recording stores whose track it is: the publisher's
  identity (a control-plane participant id, or the agent), and whether the
  track is audio (`TrackInfo.type`, `ParticipantInfo.kind` in
  `livekit_models.proto`, read as enum names or numbers). The server checks
  `roomAdmin` on that one room.
- `list-egress.json`: `Egress/ListEgress` (`ListEgressRequest`) naming one
  egress id, under a token carrying `roomRecord` and nothing else (the
  server's `EnsureRecordPermission`). A finished recording is
  `EGRESS_COMPLETE` and its object key is `file_results[0].filename`, which
  the egress service sets to the storage path after substituting `{utc}` and
  appending the extension (`updateFilepath` in `livekit/egress`
  `pkg/config/output_file.go`). The control plane hands that object out as an
  S3 SigV4 presigned GET (`presign.go`), checked against the AWS example
  signature and a botocore-signed MinIO URL.
