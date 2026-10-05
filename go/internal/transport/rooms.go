package transport

import (
	"context"
	"encoding/json"

	"github.com/punk-raven/dafter/go/internal/errs"
)

func (l *LiveKit) OpenRooms(ctx context.Context, rooms []string) (map[string]bool, error) {
	open := map[string]bool{}
	if len(rooms) == 0 {
		return open, nil
	}
	token, err := l.serviceToken(serviceGrant{RoomList: true})
	if err != nil {
		return nil, err
	}
	raw, err := l.call(ctx, twirpRoomPrefix+methodListRooms, token, listRoomsRequest{Names: rooms})
	if err != nil {
		return nil, err
	}
	var listed listRoomsJSON
	if err := json.Unmarshal(raw, &listed); err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "decode %s response", methodListRooms)
	}
	for _, r := range listed.Rooms {
		open[r.Name] = true
	}
	return open, nil
}
