package transport

import (
	"context"
	"encoding/json"
	"strings"
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
)

const (
	methodListParticipants = "ListParticipants"
	methodListEgress       = "ListEgress"
	egressComplete         = "EGRESS_COMPLETE"
	participantKindAgent   = "AGENT"
	trackTypeAudio         = "AUDIO"
)

type listParticipantsRequest struct {
	Room string `json:"room"`
}

type listEgressRequest struct {
	EgressID string `json:"egressId"`
}

type protoEnum string

var (
	participantKinds = map[int]string{0: "STANDARD", 1: "INGRESS", 2: "EGRESS", 3: "SIP", 4: participantKindAgent, 7: "CONNECTOR", 8: "BRIDGE"}
	trackTypes       = map[int]string{0: trackTypeAudio, 1: "VIDEO", 2: "DATA"}
)

func (e *protoEnum) decode(raw json.RawMessage, numbers map[int]string) error {
	if len(raw) == 0 || string(raw) == "null" {
		*e = protoEnum(numbers[0])
		return nil
	}
	var name string
	if err := json.Unmarshal(raw, &name); err == nil {
		*e = protoEnum(name)
		return nil
	}
	var n int
	if err := json.Unmarshal(raw, &n); err != nil {
		return err
	}
	*e = protoEnum(numbers[n])
	return nil
}

type trackJSON struct {
	SID  string          `json:"sid"`
	Type json.RawMessage `json:"type"`
}

type participantJSON struct {
	Identity string          `json:"identity"`
	Kind     json.RawMessage `json:"kind"`
	Tracks   []trackJSON     `json:"tracks"`
}

type listParticipantsJSON struct {
	Participants []participantJSON `json:"participants"`
}

type fileInfoJSON struct {
	Filename string `json:"filename"`
	Location string `json:"location"`
}

type listedEgressJSON struct {
	EgressID      string         `json:"egress_id"`
	EgressIDCamel string         `json:"egressId"`
	Status        string         `json:"status"`
	FileResults   []fileInfoJSON `json:"file_results"`
	FileResultsC  []fileInfoJSON `json:"fileResults"`
	File          *fileInfoJSON  `json:"file"`
}

type listEgressJSON struct {
	Items []listedEgressJSON `json:"items"`
}

func (l *LiveKit) TrackOwner(ctx context.Context, room, trackID string) (TrackPublisher, error) {
	if room == "" || trackID == "" {
		return TrackPublisher{}, errs.Errorf(errs.CodeInvalidConfig, "finding a track's publisher needs a room and a track id")
	}
	token, err := l.serviceToken(serviceGrant{RoomAdmin: true, Room: room})
	if err != nil {
		return TrackPublisher{}, err
	}
	raw, err := l.call(ctx, twirpRoomPrefix+methodListParticipants, token, listParticipantsRequest{Room: room})
	if err != nil {
		return TrackPublisher{}, err
	}
	var parsed listParticipantsJSON
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return TrackPublisher{}, errs.Wrap(errs.CodeInternal, err, "decode %s response", methodListParticipants)
	}
	for _, p := range parsed.Participants {
		for _, t := range p.Tracks {
			if t.SID != trackID {
				continue
			}
			var kind, kindOfTrack protoEnum
			if err := kind.decode(p.Kind, participantKinds); err != nil {
				return TrackPublisher{}, errs.Wrap(errs.CodeInternal, err, "decode a participant's kind")
			}
			if err := kindOfTrack.decode(t.Type, trackTypes); err != nil {
				return TrackPublisher{}, errs.Wrap(errs.CodeInternal, err, "decode a track's type")
			}
			return TrackPublisher{
				Identity: p.Identity,
				Agent:    kind == participantKindAgent,
				Audio:    kindOfTrack == trackTypeAudio,
			}, nil
		}
	}
	return TrackPublisher{}, errs.Errorf(errs.CodeInvalidConfig, "no participant in the room publishes that track")
}

func (l *LiveKit) RecordingFile(ctx context.Context, egressID string, ttl time.Duration) (RecordingFile, error) {
	if egressID == "" {
		return RecordingFile{}, errs.Errorf(errs.CodeInvalidConfig, "locating a recording needs its egress id")
	}
	if l.storage == nil {
		return RecordingFile{}, errs.Errorf(errs.CodeInvalidConfig, "egress storage is not configured, so no recording can be read back")
	}
	token, err := l.serviceToken(serviceGrant{RoomRecord: true})
	if err != nil {
		return RecordingFile{}, err
	}
	raw, err := l.call(ctx, twirpPrefix+methodListEgress, token, listEgressRequest{EgressID: egressID})
	if err != nil {
		return RecordingFile{}, err
	}
	var parsed listEgressJSON
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return RecordingFile{}, errs.Wrap(errs.CodeInternal, err, "decode %s response", methodListEgress)
	}
	for _, item := range parsed.Items {
		if first(item.EgressID, item.EgressIDCamel) != egressID {
			continue
		}
		out := RecordingFile{EgressID: egressID, Status: item.Status}
		if item.Status != egressComplete {
			return out, nil
		}
		key := objectKey(item)
		if key == "" {
			return RecordingFile{}, errs.Errorf(errs.CodeInternal, "the media server reports a complete recording with no file")
		}
		now := l.now().UTC().Truncate(time.Second)
		signed, err := PresignGet(*l.storage, key, now, ttl)
		if err != nil {
			return RecordingFile{}, err
		}
		out.Complete, out.Key, out.URL, out.ExpiresAt = true, key, signed, now.Add(ttl).UTC()
		return out, nil
	}
	return RecordingFile{}, errs.Errorf(errs.CodeInvalidConfig, "the media server knows no recording under that id")
}

func objectKey(item listedEgressJSON) string {
	results := item.FileResults
	if len(results) == 0 {
		results = item.FileResultsC
	}
	if len(results) == 0 && item.File != nil {
		results = []fileInfoJSON{*item.File}
	}
	if len(results) == 0 {
		return ""
	}
	return strings.TrimPrefix(results[0].Filename, "/")
}
