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
