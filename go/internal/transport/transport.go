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

// EgressRequest is one recording to start. The layout picks the media
// server request type; the session id and layout name the file so a stored
// object explains itself without the manifest.
type EgressRequest struct {
	Room      string
	SessionID string
	Layout    config.EgressLayout

	// AudioOnly is set when the session publishes no video. Composite layouts
	// only; a track egress records whatever the track is.
	AudioOnly bool

	// CreateRoom is set when the room may not exist yet, which is the case
	// for a recording started at session creation: the media server refuses
	// an egress on a room nobody has joined, so the room is created first and
	// the egress attaches before the first participant can. Room composite
	// only. Left unset, a start on a room that has since closed is refused
	// rather than quietly recording an empty one.
	CreateRoom bool

	// Encoding applies to the composite layouts. A track egress copies the
	// published bytes and has no encode to configure, so it is ignored there.
	Encoding *config.EgressProfile

	// Track ids, opaque and minted by the media server. track_composite takes
	// one or both; track takes exactly one.
	AudioTrackID string
	VideoTrackID string
	TrackID      string
}

// EgressInfo is what the media server reports about one recording. Status is
// the server's own state name (EGRESS_STARTING, EGRESS_ACTIVE, EGRESS_ENDING,
// EGRESS_COMPLETE, EGRESS_FAILED, EGRESS_ABORTED, EGRESS_LIMIT_REACHED),
// passed through rather than mapped so the control plane never claims a
// state the server did not.
type EgressInfo struct {
	EgressID  string
	Room      string
	Status    string
	StartedAt time.Time
	EndedAt   time.Time
	Error     string
}

// EgressStorage is where recordings land. Credentials reach the process
// through the environment only; nothing here is ever stored or returned.
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
