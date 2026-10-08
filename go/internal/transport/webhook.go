package transport

import (
	"crypto/sha256"
	"crypto/subtle"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"github.com/punk-raven/dafter/go/internal/errs"
)

const (
	EventTrackPublished    = "track_published"
	EventParticipantJoined = "participant_joined"
	EventEgressEnded       = "egress_ended"
	EventRoomFinished      = "room_finished"
	maxWebhookBytes        = 1 << 20
)

type Webhook struct {
	Event       string
	Room        string
	TrackID     string
	Participant string
	Egress      EgressInfo
	At          time.Time
}

type webhookClaims struct {
	jwt.RegisteredClaims
	SHA256 string `json:"sha256"`
}

type webhookJSON struct {
	Event string `json:"event"`
	Room  struct {
		Name string `json:"name"`
	} `json:"room"`
	Track struct {
		SID string `json:"sid"`
	} `json:"track"`
	Participant struct {
		Identity string `json:"identity"`
	} `json:"participant"`
	EgressInfo *egressInfoJSON `json:"egressInfo"`
	CreatedAt  int64JSON       `json:"createdAt"`
}

func (l *LiveKit) ReadWebhook(r *http.Request) (Webhook, error) {
	body, err := io.ReadAll(http.MaxBytesReader(nil, r.Body, maxWebhookBytes))
	if err != nil {
		return Webhook{}, errs.Wrap(errs.CodeInvalidConfig, err, "read webhook")
	}
	var claims webhookClaims
	token := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	_, err = jwt.ParseWithClaims(token, &claims, func(*jwt.Token) (any, error) {
		return []byte(l.secret), nil
	}, jwt.WithValidMethods([]string{jwt.SigningMethodHS256.Alg()}), jwt.WithIssuer(l.key), jwt.WithExpirationRequired())
	if err != nil {
		return Webhook{}, errs.Wrap(errs.CodeAuthenticationFailed, err, "webhook is not signed by the media server")
	}
	sum := sha256.Sum256(body)
	if subtle.ConstantTimeCompare([]byte(claims.SHA256), []byte(base64.StdEncoding.EncodeToString(sum[:]))) != 1 {
		return Webhook{}, errs.Errorf(errs.CodeAuthenticationFailed, "webhook body is not the one the media server signed")
	}
	var parsed webhookJSON
	if err := json.Unmarshal(body, &parsed); err != nil {
		return Webhook{}, errs.Wrap(errs.CodeInvalidConfig, err, "decode webhook")
	}
	hook := Webhook{
		Event: parsed.Event, Room: parsed.Room.Name, TrackID: parsed.Track.SID,
		Participant: parsed.Participant.Identity,
	}
	if parsed.CreatedAt > 0 {
		hook.At = time.Unix(int64(parsed.CreatedAt), 0).UTC()
	}
	if parsed.EgressInfo != nil {
		hook.Egress = parsed.EgressInfo.info()
		if hook.Room == "" {
			hook.Room = hook.Egress.Room
		}
	}
	return hook, nil
}
