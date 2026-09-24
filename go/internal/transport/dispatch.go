package transport

import (
	"context"
	"encoding/json"

	"github.com/punk-raven/dafter/go/internal/errs"
)

const (
	twirpDispatchPrefix  = "/twirp/livekit.AgentDispatchService/"
	methodCreateDispatch = "CreateDispatch"
	methodListDispatch   = "ListDispatch"
	methodDeleteDispatch = "DeleteDispatch"
	methodListRooms      = "ListRooms"
)

type createDispatchRequest struct {
	AgentName string `json:"agentName"`
	Room      string `json:"room"`
	Metadata  string `json:"metadata"`
}

type listDispatchRequest struct {
	Room string `json:"room"`
}

type deleteDispatchRequest struct {
	DispatchID string `json:"dispatchId"`
	Room       string `json:"room"`
}

type listRoomsRequest struct {
	Names []string `json:"names"`
}

type listRoomsJSON struct {
	Rooms []struct {
		Name string `json:"name"`
	} `json:"rooms"`
}

type listDispatchJSON struct {
	Dispatches      []dispatchJSON `json:"agent_dispatches"`
	DispatchesCamel []dispatchJSON `json:"agentDispatches"`
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

func (l *LiveKit) RecallAgents(ctx context.Context, room, pool string) ([]DispatchInfo, error) {
	if room == "" || pool == "" {
		return nil, errs.Errorf(errs.CodeInvalidConfig, "recalling an agent needs the room it was dispatched to and its worker pool")
	}
	open, err := l.roomOpen(ctx, room)
	if err != nil || !open {
		return []DispatchInfo{}, err
	}

	token, err := l.serviceToken(serviceGrant{RoomAdmin: true, Room: room})
	if err != nil {
		return nil, err
	}
	raw, err := l.call(ctx, twirpDispatchPrefix+methodListDispatch, token, listDispatchRequest{Room: room})
	if err != nil {
		return nil, err
	}
	var listed listDispatchJSON
	if err := json.Unmarshal(raw, &listed); err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "decode %s response", methodListDispatch)
	}

	recalled := []DispatchInfo{}
	for _, d := range append(listed.Dispatches, listed.DispatchesCamel...) {
		if d.ID == "" || first(d.AgentName, d.AgentNameCamel) != pool {
			continue
		}
		if _, err := l.call(ctx, twirpDispatchPrefix+methodDeleteDispatch, token, deleteDispatchRequest{DispatchID: d.ID, Room: room}); err != nil {
			return recalled, err
		}
		recalled = append(recalled, DispatchInfo{DispatchID: d.ID, Room: room, Pool: pool})
	}
	return recalled, nil
}

func (l *LiveKit) roomOpen(ctx context.Context, room string) (bool, error) {
	token, err := l.serviceToken(serviceGrant{RoomList: true})
	if err != nil {
		return false, err
	}
	raw, err := l.call(ctx, twirpRoomPrefix+methodListRooms, token, listRoomsRequest{Names: []string{room}})
	if err != nil {
		return false, err
	}
	var listed listRoomsJSON
	if err := json.Unmarshal(raw, &listed); err != nil {
		return false, errs.Wrap(errs.CodeInternal, err, "decode %s response", methodListRooms)
	}
	for _, r := range listed.Rooms {
		if r.Name == room {
			return true, nil
		}
	}
	return false, nil
}
