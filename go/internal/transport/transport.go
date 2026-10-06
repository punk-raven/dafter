package transport

import (
	"context"
	"net/http"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
)

type Grant struct {
	Room     string
	Identity string
	Role     config.Role
	TTL      time.Duration
}

type Token struct {
	JWT       string
	URL       string
	ExpiresAt time.Time
}

type EgressRequest struct {
	Room      string
	SessionID string
	Layout    config.EgressLayout

	AudioOnly bool

	CreateRoom bool

	Encoding *config.EgressProfile

	AudioTrackID string
	VideoTrackID string
	TrackID      string
}

type EgressInfo struct {
	EgressID  string
	Room      string
	Status    string
	StartedAt time.Time
	EndedAt   time.Time
	Error     string
}

type EgressStorage struct {
	Bucket         string
	Endpoint       string
	Region         string
	AccessKey      string
	Secret         string
	ForcePathStyle bool
}

type AgentDispatch struct {
	Room     string
	Pool     string
	Metadata []byte
}

type DispatchInfo struct {
	DispatchID string
	Room       string
	Pool       string
}

type TrackPublisher struct {
	Identity string
	Agent    bool
	Audio    bool
}

type RecordingFile struct {
	EgressID  string
	Status    string
	Ended     bool
	EndedAt   time.Time
	Complete  bool
	Key       string
	URL       string
	ExpiresAt time.Time
}

type Transport interface {
	MintToken(Grant) (Token, error)
	StartEgress(context.Context, EgressRequest) (EgressInfo, error)
	StopEgress(ctx context.Context, egressID string) (EgressInfo, error)
	DispatchAgent(context.Context, AgentDispatch) (DispatchInfo, error)
	RecallAgents(ctx context.Context, room, pool string) ([]DispatchInfo, error)
	TrackOwner(ctx context.Context, room, trackID string) (TrackPublisher, error)
	RecordingFile(ctx context.Context, egressID string, ttl time.Duration) (RecordingFile, error)
	PlaceCall(context.Context, PhoneCall) (CallInfo, error)
	HangUp(ctx context.Context, room, identity string) error
	OpenRooms(ctx context.Context, rooms []string) (map[string]bool, error)
	ReadWebhook(r *http.Request) (Webhook, error)
}
