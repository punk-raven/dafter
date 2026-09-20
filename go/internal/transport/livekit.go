package transport

import (
	"net/url"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

const (
	DefaultTTL = 15 * time.Minute
	maxTTL     = time.Hour
)

type videoGrant struct {
	RoomJoin       bool   `json:"roomJoin,omitempty"`
	Room           string `json:"room,omitempty"`
	CanPublish     *bool  `json:"canPublish,omitempty"`
	CanSubscribe   *bool  `json:"canSubscribe,omitempty"`
	CanPublishData *bool  `json:"canPublishData,omitempty"`
	Hidden         bool   `json:"hidden,omitempty"`
	Recorder       bool   `json:"recorder,omitempty"`
	Agent          bool   `json:"agent,omitempty"`
}

type claims struct {
	jwt.RegisteredClaims
	Identity string     `json:"identity"`
	Video    videoGrant `json:"video"`
}

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

	issued := l.now()
	expires := issued.Add(ttl)
	signed, err := jwt.NewWithClaims(jwt.SigningMethodHS256, &claims{
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer:    l.key,
			Subject:   g.Identity,
			IssuedAt:  jwt.NewNumericDate(issued),
			NotBefore: jwt.NewNumericDate(issued),
			ExpiresAt: jwt.NewNumericDate(expires),
		},
		Identity: g.Identity,
		Video:    *video,
	}).SignedString([]byte(l.secret))
	if err != nil {
		return Token{}, errs.Wrap(errs.CodeInternal, err, "mint join token")
	}
	return Token{JWT: signed, URL: l.url, ExpiresAt: expires}, nil
}

func ptr(b bool) *bool { return &b }

func grantsFor(role config.Role) (*videoGrant, error) {
	switch role {
	case config.RoleParticipant, config.RolePresenter:
		return &videoGrant{
			RoomJoin: true, CanPublish: ptr(true), CanSubscribe: ptr(true), CanPublishData: ptr(true),
		}, nil
	case config.RoleObserver:
		return &videoGrant{
			RoomJoin: true, CanPublish: ptr(false), CanSubscribe: ptr(true), CanPublishData: ptr(false),
		}, nil
	case config.RoleAgent:
		return &videoGrant{
			RoomJoin: true, CanPublish: ptr(true), CanSubscribe: ptr(true), CanPublishData: ptr(true),
			Agent: true,
		}, nil
	case config.RoleRecorder:
		return &videoGrant{
			RoomJoin: true, CanPublish: ptr(false), CanSubscribe: ptr(true), CanPublishData: ptr(false),
			Hidden: true, Recorder: true,
		}, nil
	default:
		return nil, errs.Errorf(errs.CodeInvalidConfig, "no grants are defined for this role")
	}
}
