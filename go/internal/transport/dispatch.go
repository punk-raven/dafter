package transport

import (
	"context"
	"encoding/json"

	"github.com/punk-raven/dafter/go/internal/errs"
)

const (
	twirpDispatchPrefix  = "/twirp/livekit.AgentDispatchService/"
	methodCreateDispatch = "CreateDispatch"
)

type createDispatchRequest struct {
	AgentName string `json:"agentName"`
	Room      string `json:"room"`
	Metadata  string `json:"metadata"`
}

type dispatchJSON struct {
	ID             string `json:"id"`
	AgentName      string `json:"agent_name"`
	AgentNameCamel string `json:"agentName"`
	Room           string `json:"room"`
}

func (l *LiveKit) DispatchAgent(ctx context.Context, d AgentDispatch) (DispatchInfo, error) {
	if d.Room == "" || d.Pool == "" {
		return DispatchInfo{}, errs.Errorf(errs.CodeInvalidConfig, "an agent dispatch needs a room and a worker pool")
	}
	if len(d.Metadata) == 0 || !json.Valid(d.Metadata) {
		return DispatchInfo{}, errs.Errorf(errs.CodeInvalidConfig, "an agent dispatch carries the resolved session config, and this one carries no valid document")
	}

	token, err := l.serviceToken(serviceGrant{RoomAdmin: true, Room: d.Room})
	if err != nil {
		return DispatchInfo{}, err
	}
	raw, err := l.call(ctx, twirpDispatchPrefix+methodCreateDispatch, token, createDispatchRequest{
		AgentName: d.Pool,
		Room:      d.Room,
		Metadata:  string(d.Metadata),
	})
	if err != nil {
		return DispatchInfo{}, err
	}

	var parsed dispatchJSON
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return DispatchInfo{}, errs.Wrap(errs.CodeInternal, err, "decode %s response", methodCreateDispatch)
	}
	if parsed.ID == "" {
		return DispatchInfo{}, errs.Errorf(errs.CodeInternal, "the media server answered %s without a dispatch id", methodCreateDispatch)
	}
	return DispatchInfo{
		DispatchID: parsed.ID,
		Room:       parsed.Room,
		Pool:       first(parsed.AgentName, parsed.AgentNameCamel),
	}, nil
}
