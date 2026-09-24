# Agent dispatch request fixture

The exact JSON the adapter sends to the media server's Twirp
`AgentDispatchService/CreateDispatch`, asserted byte-for-byte by
`go/internal/transport/transport_test.go`. Field names are the protobuf JSON
names of `CreateAgentDispatchRequest` in `livekit_agent_dispatch.proto`.

- `create-dispatch.json`: `agentName` is the worker pool from `agent.pool`,
  `room` is the session's room, and `metadata` is the resolved session config
  as a string, exactly the bytes that were hashed and stored. The worker
  validates and re-hashes it; it never asks the control plane for anything
  else.

The server creates the room when it does not exist yet, so a dispatch needs no
separate `CreateRoom`. It checks `roomAdmin` on that one room, which is why the
per-call service token for this request carries that grant and nothing else.

Recalling the agent (`RecallAgents`, behind `POST /sessions/{id}/agent/start`
and `/agent/stop`) first asks whether the room is open, then lists its
dispatches and deletes the ones of the session's worker pool:

- `list-rooms.json`: `ListRoomsRequest` names the one room, under a service
  token carrying `roomList` and nothing else. A closed room has nothing to
  recall; asking its dispatches instead would wait for a node that hosts no
  such room and fail.
- `list-dispatch.json`: `ListAgentDispatchRequest` names only the room.
- `delete-dispatch.json`: `DeleteAgentDispatchRequest` names a dispatch id the
  list returned and the room. The media server terminates that dispatch's
  jobs and removes the agent participant.

Every room also carries a default dispatch with an empty agent name, for
workers that register without one. It is not ours, so it is never deleted.
Listing and deleting run under the same room-scoped `roomAdmin` token as
`CreateDispatch`.
