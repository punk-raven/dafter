package control

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/ids"
	"github.com/punk-raven/dafter/go/internal/state"
	"github.com/punk-raven/dafter/go/internal/transport"
	"github.com/punk-raven/dafter/go/internal/turn"
)

type Service struct {
	Catalog   *config.Catalog
	Store     state.SessionStore
	Transport transport.Transport
	TURN      *turn.Fetcher
	TokenTTL  time.Duration
	Log       *slog.Logger

	WorkerSecret string
}

func (s *Service) Handler() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("POST /sessions", s.createSession)
	mux.HandleFunc("GET /sessions/{sessionID}", s.readSession)
	mux.HandleFunc("POST /sessions/{sessionID}/join", s.joinSession)
	mux.HandleFunc("POST /sessions/{sessionID}/recording/start", s.startRecording)
	mux.HandleFunc("POST /sessions/{sessionID}/recording/stop", s.stopRecording)
	mux.HandleFunc("POST /sessions/{sessionID}/agent/start", s.inviteAgent)
	mux.HandleFunc("POST /sessions/{sessionID}/agent/stop", s.removeAgent)
	mux.HandleFunc("POST /sessions/{sessionID}/agent/key", s.agentKey)
	mux.HandleFunc("POST /sessions/{sessionID}/agent/refusal", s.agentRefusal)
	return mux
}

type createSessionRequest struct {
	TenantID  string          `json:"tenantId"`
	Profile   string          `json:"profile,omitempty"`
	Language  string          `json:"language"`
	Channel   config.Channel  `json:"channel"`
	Role      config.Role     `json:"role,omitempty"`
	Overrides json.RawMessage `json:"overrides,omitempty"`
}

type createSessionResponse struct {
	SessionID     string           `json:"sessionId"`
	ParticipantID string           `json:"participantId"`
	Room          string           `json:"room"`
	ConfigHash    string           `json:"configHash"`
	Config        json.RawMessage  `json:"config"`
	Token         string           `json:"token"`
	URL           string           `json:"url"`
	ExpiresAt     time.Time        `json:"expiresAt"`
	ICEServers    []turn.ICEServer `json:"iceServers,omitempty"`

	EncryptionKey string `json:"encryptionKey,omitempty"`

	AgentDispatchID string `json:"agentDispatchId,omitempty"`
}

func mintEncryptionKey() (string, error) {
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return "", errs.Wrap(errs.CodeInternal, err, "mint encryption key")
	}
	return base64.RawURLEncoding.EncodeToString(b), nil
}

func keyFor(cfg *config.ResolvedSessionConfig, sess state.Session, role config.Role) string {
	if sess.EncryptionKey == "" || !cfg.PrivacyMode.DisclosesKeyTo(role) {
		return ""
	}
	return sess.EncryptionKey
}

func (s *Service) createSession(w http.ResponseWriter, r *http.Request) {
	var req createSessionRequest
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
	d.DisallowUnknownFields()
	if err := d.Decode(&req); err != nil {
		s.fail(w, errs.Wrap(errs.CodeInvalidConfig, err, "decode session request"))
		return
	}
	if req.Role == "" {
		req.Role = config.RoleParticipant
	}

	sessionID, err := ids.NewID(ids.PrefixSession)
	if err != nil {
		s.fail(w, errs.Wrap(errs.CodeInternal, err, "mint session id"))
		return
	}
	participantID, err := ids.NewID(ids.PrefixParticipant)
	if err != nil {
		s.fail(w, errs.Wrap(errs.CodeInternal, err, "mint participant id"))
		return
	}

	resolved, err := s.Catalog.Resolve(config.Request{
		SessionID: sessionID,
		TenantID:  req.TenantID,
		Profile:   req.Profile,
		Language:  req.Language,
		Channel:   req.Channel,
		Overrides: req.Overrides,
	})
	if err != nil {
		s.fail(w, err)
		return
	}

	sess := state.Session{
		SessionID:  sessionID,
		TenantID:   resolved.Config.TenantID,
		Room:       sessionID,
		ConfigHash: resolved.Hash,
		Config:     resolved.Document,
		CreatedAt:  time.Now().UTC(),
	}
	if resolved.Config.MintsSharedKey() {
		key, err := mintEncryptionKey()
		if err != nil {
			s.fail(w, err)
			return
		}
		sess.EncryptionKey = key
	}
	if err := s.Store.CreateSession(r.Context(), sess); err != nil {
		s.fail(w, err)
		return
	}

	if rec := resolved.Config.Recording; rec.Enabled && rec.StartAt == config.StartAtSessionCreate {
		if _, err := s.startEgress(r.Context(), sess, resolved.Config, startRecordingRequest{}, true); err != nil {
			s.fail(w, err)
			return
		}
	}

	dispatchID, err := s.dispatchAgent(r.Context(), sess, resolved.Config)
	if err != nil {
		s.fail(w, err)
		return
	}

	token, err := s.Transport.MintToken(transport.Grant{
		Room:     sessionID,
		Identity: participantID,
		Role:     req.Role,
		TTL:      s.TokenTTL,
	})
	if err != nil {
		s.fail(w, err)
		return
	}

	var iceServers []turn.ICEServer
	if s.TURN != nil && s.TURN.Enabled() {
		servers, err := s.TURN.FetchCredentials(r.Context())
		if err != nil {
			s.log().Warn("turn credential fetch failed, proceeding without ice servers", "error", err)
		} else {
			iceServers = servers
		}
	}

	s.write(w, http.StatusCreated, createSessionResponse{
		SessionID:     sessionID,
		ParticipantID: participantID,
		Room:          sessionID,
		ConfigHash:    resolved.Hash,
		Config:        resolved.Document,
		Token:         token.JWT,
		URL:           token.URL,
		ExpiresAt:     token.ExpiresAt,
		ICEServers:    iceServers,
		EncryptionKey: keyFor(resolved.Config, sess, req.Role),

		AgentDispatchID: dispatchID,
	})
}

func (s *Service) dispatchAgent(ctx context.Context, sess state.Session, cfg *config.ResolvedSessionConfig) (string, error) {
	if !cfg.Agent.Enabled {
		return "", nil
	}
	info, err := s.Transport.DispatchAgent(ctx, transport.AgentDispatch{
		Room:     sess.Room,
		Pool:     cfg.Agent.Pool,
		Metadata: sess.Config,
	})
	if err != nil {
		incDispatch(false)
		return "", err
	}
	incDispatch(true)
	s.log().Info("agent dispatched", "session", sess.SessionID, "pool", cfg.Agent.Pool, "dispatch", info.DispatchID)
	return info.DispatchID, nil
}

type joinSessionRequest struct {
	Role config.Role `json:"role,omitempty"`
}

func (s *Service) joinSession(w http.ResponseWriter, r *http.Request) {
	sessionID := r.PathValue("sessionID")
	if err := ids.ValidateID(ids.PrefixSession, sessionID); err != nil {
		s.fail(w, errs.Wrap(errs.CodeInvalidConfig, err, "invalid session id"))
		return
	}

	sess, err := s.Store.Session(r.Context(), sessionID)
	if err != nil {
		s.fail(w, errs.Wrap(errs.CodeInvalidConfig, err, "session not found"))
		return
	}
	cfg, err := config.Parse(sess.Config)
	if err != nil {
		s.fail(w, errs.Wrap(errs.CodeInternal, err, "stored session document"))
		return
	}

	var req joinSessionRequest
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20))
	d.DisallowUnknownFields()
	if err := d.Decode(&req); err != nil {
		s.fail(w, errs.Wrap(errs.CodeInvalidConfig, err, "decode join request"))
		return
	}
	if req.Role == "" {
		req.Role = config.RoleParticipant
	}

	participantID, err := ids.NewID(ids.PrefixParticipant)
	if err != nil {
		s.fail(w, errs.Wrap(errs.CodeInternal, err, "mint participant id"))
		return
	}

	token, err := s.Transport.MintToken(transport.Grant{
		Room:     sess.Room,
		Identity: participantID,
		Role:     req.Role,
		TTL:      s.TokenTTL,
	})
	if err != nil {
		s.fail(w, err)
		return
	}

	var iceServers []turn.ICEServer
	if s.TURN != nil && s.TURN.Enabled() {
		servers, err := s.TURN.FetchCredentials(r.Context())
		if err != nil {
			s.log().Warn("turn credential fetch failed, proceeding without ice servers", "error", err)
		} else {
			iceServers = servers
		}
	}

	s.write(w, http.StatusOK, createSessionResponse{
		SessionID:     sessionID,
		ParticipantID: participantID,
		Room:          sess.Room,
		ConfigHash:    sess.ConfigHash,
		Config:        sess.Config,
		Token:         token.JWT,
		URL:           token.URL,
		ExpiresAt:     token.ExpiresAt,
		ICEServers:    iceServers,
		EncryptionKey: keyFor(cfg, sess, req.Role),
	})
}

func (s *Service) write(w http.ResponseWriter, status int, body any) {
	var buf bytes.Buffer
	if err := json.NewEncoder(&buf).Encode(body); err != nil {
		s.log().Error("encode response", "error", err)
		http.Error(w, `{"code":"internal","message":"encode response","retryable":false}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if _, err := w.Write(buf.Bytes()); err != nil {
		s.log().Error("write response", "error", err)
	}
}

func (s *Service) fail(w http.ResponseWriter, err error) {
	var de *errs.Error
	if !errors.As(err, &de) {
		de = errs.Wrap(errs.CodeInternal, err, "request failed")
	}
	incError(de.Code)
	s.log().Warn("request rejected", "code", de.Code, "details", de.Details)
	s.write(w, statusFor(de.Code), de)
}

func (s *Service) log() *slog.Logger {
	if s.Log != nil {
		return s.Log
	}
	return slog.Default()
}

func statusFor(code errs.ErrorCode) int {
	switch code {
	case errs.CodeInvalidConfig, errs.CodeConsentRequired, errs.CodePrivacyModeForbids,
		errs.CodeUnsupportedCapability, errs.CodeResidencyViolation:
		return http.StatusBadRequest
	case errs.CodeAuthenticationFailed:
		return http.StatusUnauthorized
	case errs.CodeQuotaExceeded, errs.CodeBudgetExceeded, errs.CodeRateLimited:
		return http.StatusTooManyRequests
	case errs.CodeProviderUnavailable, errs.CodeProviderTimeout:
		return http.StatusServiceUnavailable
	default:
		return http.StatusInternalServerError
	}
}
