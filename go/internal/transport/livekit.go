package transport

import (
	"net/url"
	"time"

	"github.com/livekit/protocol/auth"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

const (
	DefaultTTL = 15 * time.Minute
	maxTTL     = time.Hour
)

type LiveKit struct {
	url    string
	key    string
	secret string
	now    func() time.Time
}

func NewLiveKit(serverURL, apiKey, apiSecret string) (*LiveKit, error) {
	u, err := url.Parse(serverURL)
	if err != nil || u.Host == "" || (u.Scheme != "ws" && u.Scheme != "wss") {
		return nil, errs.Errorf(errs.CodeInvalidConfig, "media server url must be ws:// or wss://")
	}
	if apiKey == "" || apiSecret == "" {
		return nil, errs.Errorf(errs.CodeAuthenticationFailed, "media server api key and secret are required")
	}
	return &LiveKit{url: serverURL, key: apiKey, secret: apiSecret, now: time.Now}, nil
}

func (l *LiveKit) MintToken(g Grant) (Token, error) {
	if g.Room == "" || g.Identity == "" {
		return Token{}, errs.Errorf(errs.CodeInvalidConfig, "a token needs a room and an identity")
	}
	video, err := grantsFor(g.Role)
	if err != nil {
		return Token{}, err
	}
	video.Room = g.Room

	ttl := g.TTL
	if ttl <= 0 {
		ttl = DefaultTTL
	}
	if ttl > maxTTL {
		return Token{}, errs.Errorf(errs.CodeInvalidConfig,
			"a join token lives at most %s; a long-lived token cannot be revoked", maxTTL)
	}

	jwt, err := auth.NewAccessToken(l.key, l.secret).
		SetVideoGrant(video).
		SetIdentity(g.Identity).
		SetValidFor(ttl).
		ToJWT()
	if err != nil {
		return Token{}, errs.Wrap(errs.CodeInternal, err, "mint join token")
	}
	return Token{JWT: jwt, URL: l.url, ExpiresAt: l.now().Add(ttl)}, nil
}

func ptr(b bool) *bool { return &b }

func grantsFor(role config.Role) (*auth.VideoGrant, error) {
	switch role {
	case config.RoleParticipant, config.RolePresenter:
		return &auth.VideoGrant{
			RoomJoin: true, CanPublish: ptr(true), CanSubscribe: ptr(true), CanPublishData: ptr(true),
		}, nil
	case config.RoleObserver:
		return &auth.VideoGrant{
			RoomJoin: true, CanPublish: ptr(false), CanSubscribe: ptr(true), CanPublishData: ptr(false),
		}, nil
	case config.RoleAgent:
		return &auth.VideoGrant{
			RoomJoin: true, CanPublish: ptr(true), CanSubscribe: ptr(true), CanPublishData: ptr(true),
			Agent: true,
		}, nil
	case config.RoleRecorder:
		return &auth.VideoGrant{
			RoomJoin: true, CanPublish: ptr(false), CanSubscribe: ptr(true), CanPublishData: ptr(false),
			Hidden: true, Recorder: true,
		}, nil
	default:
		return nil, errs.Errorf(errs.CodeInvalidConfig, "no grants are defined for this role")
	}
}
