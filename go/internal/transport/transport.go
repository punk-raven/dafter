package transport

import (
	"context"
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

type Transport interface {
	MintToken(Grant) (Token, error)
	StartEgress(context.Context, EgressRequest) (EgressInfo, error)
	StopEgress(ctx context.Context, egressID string) (EgressInfo, error)
}
