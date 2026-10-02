package transport

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/punk-raven/dafter/go/internal/errs"
)

var (
	ErrNoSuchParticipant = errors.New("transport: nobody under that identity is in the room")
	ErrNotAPhone         = errors.New("transport: the participant is not on a phone")
)

const (
	methodRemoveParticipant = "RemoveParticipant"
	participantKindSIP      = "SIP"
)

type removeParticipantRequest struct {
	Room     string `json:"room"`
	Identity string `json:"identity"`
}

func (l *LiveKit) HangUp(ctx context.Context, room, identity string) error {
	if room == "" || identity == "" {
		return errs.Errorf(errs.CodeInvalidConfig, "hanging up a phone names the room it is in and its identity")
	}
	open, err := l.roomOpen(ctx, room)
	if err != nil {
		return err
	}
	if !open {
		return ErrNoSuchParticipant
	}
	token, err := l.serviceToken(serviceGrant{RoomAdmin: true, Room: room})
	if err != nil {
		return err
	}
	kind, err := l.participantKind(ctx, token, room, identity)
	if err != nil {
		return err
	}
	if kind != participantKindSIP {
		return ErrNotAPhone
	}
	_, err = l.call(ctx, twirpRoomPrefix+methodRemoveParticipant, token, removeParticipantRequest{Room: room, Identity: identity})
	return err
}

func (l *LiveKit) participantKind(ctx context.Context, token, room, identity string) (protoEnum, error) {
	raw, err := l.call(ctx, twirpRoomPrefix+methodListParticipants, token, listParticipantsRequest{Room: room})
	if err != nil {
		return "", err
	}
	var parsed listParticipantsJSON
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return "", errs.Wrap(errs.CodeInternal, err, "decode %s response", methodListParticipants)
	}
	for _, p := range parsed.Participants {
		if p.Identity != identity {
			continue
		}
		var kind protoEnum
		if err := kind.decode(p.Kind, participantKinds); err != nil {
			return "", errs.Wrap(errs.CodeInternal, err, "decode a participant's kind")
		}
		return kind, nil
	}
	return "", ErrNoSuchParticipant
}
