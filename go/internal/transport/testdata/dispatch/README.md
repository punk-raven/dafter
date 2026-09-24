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
