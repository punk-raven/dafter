# SIP request fixture

The exact JSON the adapter sends to the media server's Twirp
`SIP/CreateSIPParticipant` to place an outbound phone call, asserted
byte-for-byte by `go/internal/transport/sip_test.go`. Field names are the
protobuf JSON names of `CreateSIPParticipantRequest` in `livekit_sip.proto`;
the server reads them with protojson, so enums are their names and durations
are strings in seconds.

- `create-sip-participant.json`: the call goes out on an inline `trunk`
  (`SIPOutboundConfig`) built from the operator's trunk table, so no trunk is
  stored in the media server and the table stays the one place a trunk is
  configured. `sipNumber` is the first of the trunk's own numbers, which
  inline trunks require because they carry no number list. The credentials
  here are made up; real ones come from the environment through the table's
  `secret://` references.

- `create-sip-participant-bridge.json`: the dial-back of an inbound call. The
  carrier holds the caller in a conference, and the call goes to the carrier's
  registrar (the inline trunk's host and port) as the carrier's SIP endpoint
  attached to the bridge application, authenticating with that endpoint's
  username and password (made up here, from the table's `secret://`
  references in a real call). The carrier answers a call from the endpoint
  with the endpoint's application, so `sipCallTo` (the application id) only
  names the leg. The leg carries the per-call bridge token as the
  `X-VH-Bridge` header, which the carrier passes to the application's answer
  URL as a form field of the same name; the token is what admits it to the
  right conference. The caller's own number never reaches the media server at
  all.

What keeps the dialed number out of the room: `participantIdentity` is the
opaque `p_` id the control plane minted, `participantName` is the constant
`Phone`, and `hidePhoneNumber` stops the SIP service from adding
`sip.phoneNumber`, `sip.hostname` and `sip.trunkPhoneNumber` to the
participant's attributes (`NewCreateSIPParticipantRequestResult` in
livekit/protocol `rpc/sip.go`). The number is in the request itself, which
the control plane never logs or stores.

`waitUntilAnswered` is false, so the server returns once the SIP participant
is in the room with `sip.callStatus` `dialing` and the call rings on
asynchronously (`CreateSIPParticipant` in livekit/sip `pkg/sip/client.go`);
the agent waits for `active` before it speaks.

The server checks `sip.call` (`EnsureSIPCallPermission` in livekit/livekit
`pkg/service/auth.go`), which is not scoped to a room, so the per-call service
token states `{"admin": false, "call": true}` and an empty video grant, lives
one minute and never leaves the control plane.

Hanging up one phone (`HangUp`, behind
`POST /sessions/{id}/call/{participantId}/stop`) asks whether the room is
open (`list-rooms.json` in `../dispatch/`, under a `roomList`-only token),
then lists the room's participants (`list-participants.json` in
`../egress/`) and removes the one named only when its `ParticipantInfo.kind`
is `SIP`, so a person in the browser or the agent is never hung up here:

- `remove-participant.json`: `RoomService/RemoveParticipant`
  (`RoomParticipantIdentity` in `livekit_room.proto`), the room and the
  phone's minted `p_` identity. The media server ends the SIP leg with the
  participant. The list and the removal run under one per-call service token
  carrying `roomAdmin` on that one room and nothing else, which never leaves
  the control plane.
