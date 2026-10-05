package transport

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
)

var ErrUnknownRecording = errors.New("transport: the media server knows no recording under that id")

const (
	methodListParticipants = "ListParticipants"
	methodListEgress       = "ListEgress"
	egressComplete         = "EGRESS_COMPLETE"
	egressLimitReached     = "EGRESS_LIMIT_REACHED"
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
	egressStatuses   = map[int]string{0: "EGRESS_STARTING", 1: "EGRESS_ACTIVE", 2: "EGRESS_ENDING", 3: egressComplete, 4: "EGRESS_FAILED", 5: "EGRESS_ABORTED", 6: egressLimitReached}
	egressEnded      = map[protoEnum]bool{egressComplete: true, "EGRESS_FAILED": true, "EGRESS_ABORTED": true, egressLimitReached: true}
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
	EgressID      string          `json:"egress_id"`
	EgressIDCamel string          `json:"egressId"`
	Status        json.RawMessage `json:"status"`
	EndedAt       int64JSON       `json:"ended_at"`
	EndedAtCamel  int64JSON       `json:"endedAt"`
	FileResults   []fileInfoJSON  `json:"file_results"`
	FileResultsC  []fileInfoJSON  `json:"fileResults"`
	File          *fileInfoJSON   `json:"file"`
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
		var status protoEnum
		if err := status.decode(item.Status, egressStatuses); err != nil {
			return RecordingFile{}, errs.Wrap(errs.CodeInternal, err, "decode an egress status")
		}
		out := RecordingFile{EgressID: egressID, Status: string(status), Ended: egressEnded[status]}
		if !out.Ended {
			return out, nil
		}
		out.EndedAt = nanos(int64(max(item.EndedAt, item.EndedAtCamel)))
		key := objectKey(item)
		switch {
		case key == "" && status == egressComplete:
			return RecordingFile{}, errs.Errorf(errs.CodeInternal, "the media server reports a complete recording with no file")
		case key == "" || (status != egressComplete && status != egressLimitReached):
			return out, nil
		}
		now := l.now().UTC().Truncate(time.Second)
		signed, err := PresignGet(*l.storage, key, now, ttl)
		if err != nil {
			return RecordingFile{}, err
		}
		out.Complete, out.Key, out.URL, out.ExpiresAt = true, key, signed, now.Add(ttl).UTC()
		return out, nil
	}
	return RecordingFile{}, errs.Wrap(errs.CodeInvalidConfig, ErrUnknownRecording, "the media server knows no recording under that id")
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
