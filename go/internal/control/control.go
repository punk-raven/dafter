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
	"sync"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/ids"
	"github.com/punk-raven/dafter/go/internal/state"
	"github.com/punk-raven/dafter/go/internal/transport"
	"github.com/punk-raven/dafter/go/internal/turn"
)

type CatalogSource interface {
	Snapshot() config.Snapshot
}

type Service struct {
	Catalog   CatalogSource
	Store     state.SessionStore
	Transport transport.Transport
	TURN      *turn.Fetcher
	TokenTTL  time.Duration
	Log       *slog.Logger
	Trunks    transport.Trunks

	held        heldCalls
	prompts     pinPrompts
	recordingMu sync.Mutex

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
	mux.HandleFunc("POST /sessions/{sessionID}/call/start", s.startCall)
	mux.HandleFunc("POST /sessions/{sessionID}/call/{participantID}/stop", s.stopCall)
	mux.HandleFunc("POST /telephony/{trunk}/answer", s.answerCall)
	mux.HandleFunc("POST /telephony/{trunk}/held/{token}", s.callHeld)
	mux.HandleFunc("POST /telephony/{trunk}/bridge", s.bridgeCall)
	mux.HandleFunc("POST /telephony/{trunk}/pin", s.meetingPIN)
	mux.HandleFunc("PUT /sessions/{sessionID}/dial-in/numbers", s.setAllowedNumbers)
	mux.HandleFunc("POST /sessions/{sessionID}/scribe/key", s.scribeKey)
	mux.HandleFunc("POST /sessions/{sessionID}/scribe/refusal", s.scribeRefusal)
	mux.HandleFunc("GET /sessions/{sessionID}/transcription/sources", s.transcriptionSources)
	mux.HandleFunc("POST /sessions/{sessionID}/transcripts", s.storeTranscript)
	mux.HandleFunc("GET /sessions/{sessionID}/transcripts", s.listTranscripts)
	mux.HandleFunc("GET /sessions/{sessionID}/transcripts/{version}", s.exportTranscript)
	mux.HandleFunc("POST /sessions/{sessionID}/minutes", s.storeMinutes)
	mux.HandleFunc("GET /sessions/{sessionID}/minutes", s.readMinutes)
	return mux
}

type createSessionRequest struct {
	TenantID  string          `json:"tenantId"`
	Agent     string          `json:"agent,omitempty"`
	Profile   string          `json:"profile,omitempty"`
	Language  string          `json:"language"`
	Channel   config.Channel  `json:"channel"`
	LLM       string          `json:"llm,omitempty"`
	Role      config.Role     `json:"role,omitempty"`
	Overrides json.RawMessage `json:"overrides,omitempty"`
}

type createSessionResponse struct {
	SessionID     string           `json:"sessionId"`
	ParticipantID string           `json:"participantId"`
	Room          string           `json:"room"`
	ConfigHash    string           `json:"configHash"`
	ReleaseID     int64            `json:"releaseId,omitempty"`
	Config        json.RawMessage  `json:"config"`
	Token         string           `json:"token"`
	URL           string           `json:"url"`
	ExpiresAt     time.Time        `json:"expiresAt"`
	ICEServers    []turn.ICEServer `json:"iceServers,omitempty"`

	EncryptionKey    string         `json:"encryptionKey,omitempty"`
	DialIn           *dialInDetails `json:"dialIn,omitempty"`
	AgentDispatchID  string         `json:"agentDispatchId,omitempty"`
	ScribeDispatchID string         `json:"scribeDispatchId,omitempty"`
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

	participantID, err := ids.NewID(ids.PrefixParticipant)
	if err != nil {
		s.fail(w, errs.Wrap(errs.CodeInternal, err, "mint participant id"))
		return
	}
	opened, err := s.openSession(r.Context(), config.Request{
		TenantID:  req.TenantID,
		Agent:     req.Agent,
		Profile:   req.Profile,
		Language:  req.Language,
		Channel:   req.Channel,
		LLM:       req.LLM,
		Overrides: req.Overrides,
	}, "")
	if err != nil {
		s.fail(w, err)
		return
	}
	sess, resolved, sessionID := opened.sess, opened.resolved, opened.sess.SessionID

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
		SessionID:        sessionID,
		ParticipantID:    participantID,
		Room:             sessionID,
		ConfigHash:       resolved.Hash,
		ReleaseID:        sess.ReleaseID,
		Config:           resolved.Document,
		Token:            token.JWT,
		URL:              token.URL,
		ExpiresAt:        token.ExpiresAt,
		ICEServers:       iceServers,
		EncryptionKey:    keyFor(resolved.Config, sess, req.Role),
		DialIn:           s.dialInFor(r.Context(), resolved.Config, sessionID, req.Role),
		AgentDispatchID:  opened.dispatchID,
		ScribeDispatchID: opened.scribeDispatchID,
	})
}

type openedSession struct {
	sess             state.Session
	resolved         *config.Resolution
	dispatchID       string
	scribeDispatchID string
}

func (s *Service) openSession(ctx context.Context, req config.Request, trunk string) (openedSession, error) {
	sessionID, err := ids.NewID(ids.PrefixSession)
	if err != nil {
		return openedSession{}, errs.Wrap(errs.CodeInternal, err, "mint session id")
	}
	req.SessionID = sessionID
	snapshot := s.Catalog.Snapshot()
	resolved, err := snapshot.Catalog.Resolve(req)
	if err != nil {
		return openedSession{}, err
	}
	if name := resolved.Config.TrunkName(); name != "" {
		if _, err := s.knownTrunk(name); err != nil {
			return openedSession{}, err
		}
	}
	if trunk != "" && resolved.Config.TrunkName() != trunk {
		return openedSession{}, located(errs.CodeInvalidConfig, "/telephony/trunk", "an inbound call's session names the trunk it arrived on")
	}
	if resolved.Config.TakesDialIn() {
		if _, err := s.meetingNumbers(resolved.Config); err != nil {
			return openedSession{}, err
		}
	}

	sess := state.Session{
		SessionID:  sessionID,
		TenantID:   resolved.Config.TenantID,
		Room:       sessionID,
		ConfigHash: resolved.Hash,
		ReleaseID:  snapshot.Release,
		Config:     resolved.Document,
		CreatedAt:  time.Now().UTC(),
	}
	if resolved.Config.MintsSharedKey() {
		if sess.EncryptionKey, err = mintEncryptionKey(); err != nil {
			return openedSession{}, err
		}
	}
	if err := s.Store.CreateSession(ctx, sess); err != nil {
		return openedSession{}, err
	}
	if resolved.Config.TakesDialIn() {
		if err := s.openDialIn(ctx, sess); err != nil {
			return openedSession{}, err
		}
	}
	if rec := resolved.Config.Recording; rec.Enabled && rec.StartAt == config.StartAtSessionCreate {
		if _, _, err := s.startEgress(ctx, sess, resolved.Config, startRecordingRequest{}, true, state.Egress{}); err != nil {
			return openedSession{}, err
		}
	}
	dispatchID, err := s.dispatchAgent(ctx, sess, resolved.Config)
	if err != nil {
		return openedSession{}, err
	}
	return openedSession{
		sess: sess, resolved: resolved, dispatchID: dispatchID,
		scribeDispatchID: s.dispatchScribe(ctx, sess, resolved.Config),
	}, nil
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
	Role             config.Role `json:"role,omitempty"`
	RecordingConsent string      `json:"recordingConsent,omitempty"`
}

func consentToRecording(cfg *config.ResolvedSessionConfig, given string) error {
	if !cfg.Recording.Enabled || given == cfg.Recording.ConsentArtifactID {
		return nil
	}
	return located(errs.CodeConsentRequired, "/recordingConsent",
		"this session is recorded; a joiner names the consent artifact it was shown before it is let in")
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
	if err := consentToRecording(cfg, req.RecordingConsent); err != nil {
		s.fail(w, err)
		return
	}
	s.resumeRecording(r.Context(), sess, cfg)

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
		ReleaseID:     sess.ReleaseID,
		Config:        sess.Config,
		Token:         token.JWT,
		URL:           token.URL,
		ExpiresAt:     token.ExpiresAt,
		ICEServers:    iceServers,
		EncryptionKey: keyFor(cfg, sess, req.Role),
		DialIn:        s.dialInFor(r.Context(), cfg, sessionID, req.Role),
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
