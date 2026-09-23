package transport

import (
	"net/http"
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
	url     string
	httpURL string
	key     string
	secret  string
	now     func() time.Time

	storage *EgressStorage
	client  *http.Client
}

type Option func(*LiveKit) error

func WithEgressStorage(s EgressStorage) Option {
	return func(l *LiveKit) error {
		if s.Bucket == "" || s.AccessKey == "" || s.Secret == "" {
			return errs.Errorf(errs.CodeInvalidConfig, "egress storage needs a bucket, an access key and a secret")
		}
		if s.Endpoint != "" {
			if u, err := url.Parse(s.Endpoint); err != nil || u.Host == "" || (u.Scheme != "http" && u.Scheme != "https") {
				return errs.Errorf(errs.CodeInvalidConfig, "egress storage endpoint must be http:// or https://")
			}
		}
		l.storage = &s
		return nil
	}
}

func WithHTTPClient(c *http.Client) Option {
	return func(l *LiveKit) error {
		l.client = c
		return nil
	}
}

func NewLiveKit(serverURL, apiKey, apiSecret string, opts ...Option) (*LiveKit, error) {
	u, err := url.Parse(serverURL)
	if err != nil || u.Host == "" || (u.Scheme != "ws" && u.Scheme != "wss") {
		return nil, errs.Errorf(errs.CodeInvalidConfig, "media server url must be ws:// or wss://")
	}
	if apiKey == "" || apiSecret == "" {
		return nil, errs.Errorf(errs.CodeAuthenticationFailed, "media server api key and secret are required")
	}
	httpURL := *u
	httpURL.Scheme = map[string]string{"ws": "http", "wss": "https"}[u.Scheme]
	l := &LiveKit{
		url: serverURL, httpURL: httpURL.String(), key: apiKey, secret: apiSecret,
		now:    time.Now,
		client: &http.Client{Timeout: 30 * time.Second},
	}
	for _, opt := range opts {
		if err := opt(l); err != nil {
			return nil, err
		}
	}
	return l, nil
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
