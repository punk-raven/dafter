package control_test

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/control"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/schema"
	"github.com/punk-raven/dafter/go/internal/state"
	"github.com/punk-raven/dafter/go/internal/transport"
	"github.com/punk-raven/dafter/go/internal/turn"
)

const catalogPath = "../../cmd/dafter-control/catalog.json"

const tenantID = "t_9c21a4be"

type stubTransport struct {
	grant transport.Grant
	err   error

	mu        sync.Mutex
	started   []transport.EgressRequest
	stopped   []string
	egressErr error
	nextID    int

	dispatched  []transport.AgentDispatch
	dispatchErr error
	failPool    string
	recalled    []string
	live        []string
	recallErr   error

	owners    map[string]transport.TrackPublisher
	files     map[string]transport.RecordingFile
	forgotten map[string]bool
}

func (s *stubTransport) RecallAgents(_ context.Context, room, pool string) ([]transport.DispatchInfo, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.recallErr != nil {
		return nil, s.recallErr
	}
	var out []transport.DispatchInfo
	for _, id := range s.live {
		out = append(out, transport.DispatchInfo{DispatchID: id, Room: room, Pool: pool})
		s.recalled = append(s.recalled, id)
	}
	s.live = nil
	return out, nil
}

func (s *stubTransport) DispatchAgent(_ context.Context, d transport.AgentDispatch) (transport.DispatchInfo, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.dispatchErr != nil {
		return transport.DispatchInfo{}, s.dispatchErr
	}
	if d.Pool == s.failPool {
		return transport.DispatchInfo{}, errs.Errorf(errs.CodeProviderUnavailable, "no worker of pool %s answered", d.Pool)
	}
	s.dispatched = append(s.dispatched, d)
	id := fmt.Sprintf("AD_stub%d", len(s.dispatched))
	s.live = append(s.live, id)
	return transport.DispatchInfo{DispatchID: id, Room: d.Room, Pool: d.Pool}, nil
}

func (s *stubTransport) MintToken(g transport.Grant) (transport.Token, error) {
	s.grant = g
	if s.err != nil {
		return transport.Token{}, s.err
	}
	return transport.Token{
		JWT:       "stub." + g.Room + "." + g.Identity,
		URL:       "ws://127.0.0.1:7880",
		ExpiresAt: time.Now().Add(g.TTL),
	}, nil
}

func (s *stubTransport) StartEgress(_ context.Context, req transport.EgressRequest) (transport.EgressInfo, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.egressErr != nil {
		return transport.EgressInfo{}, s.egressErr
	}
	s.started = append(s.started, req)
	s.nextID++
	return transport.EgressInfo{
		EgressID: fmt.Sprintf("EG_stub%d", s.nextID), Room: req.Room, Status: "EGRESS_STARTING",
		StartedAt: time.Date(2026, 9, 22, 10, 0, s.nextID, 0, time.UTC),
	}, nil
}

func (s *stubTransport) StopEgress(_ context.Context, egressID string) (transport.EgressInfo, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.egressErr != nil {
		return transport.EgressInfo{}, s.egressErr
	}
	if f, ok := s.files[egressID]; ok && f.Ended {
		return transport.EgressInfo{}, errs.Errorf(errs.CodeInvalidConfig, "media server refused StopEgress (http 412, failed_precondition)")
	}
	s.stopped = append(s.stopped, egressID)
	return transport.EgressInfo{EgressID: egressID, Status: "EGRESS_ENDING",
		EndedAt: time.Date(2026, 9, 22, 10, 5, 0, 0, time.UTC)}, nil
}

func (s *stubTransport) egresses() ([]transport.EgressRequest, []string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return append([]transport.EgressRequest(nil), s.started...), append([]string(nil), s.stopped...)
}

type harness struct {
	server    *httptest.Server
	store     *state.Store
	transport *stubTransport
	svc       *control.Service
}

const workerSecret = "worker-secret-for-tests"

func serve(t *testing.T) *harness {
	t.Helper()
	raw, err := os.ReadFile(catalogPath)
	if err != nil {
		t.Fatalf("read catalog: %v", err)
	}
	catalog, err := config.LoadCatalog(raw)
	if err != nil {
		t.Fatalf("load catalog: %v", err)
	}
	store, err := state.Open(t.Context(), filepath.Join(t.TempDir(), "dafter.db"))
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() {
		if err := store.Close(); err != nil {
			t.Errorf("close store: %v", err)
		}
	})

	tport := &stubTransport{}
	svc := &control.Service{
		Catalog: catalog, Store: store, Transport: tport, TokenTTL: 15 * time.Minute,
		WorkerSecret: workerSecret,
	}
	server := httptest.NewServer(svc.MetricsHandler())
	t.Cleanup(server.Close)
	return &harness{server: server, store: store, transport: tport, svc: svc}
}

type sessionResponse struct {
	SessionID     string           `json:"sessionId"`
	ParticipantID string           `json:"participantId"`
	Room          string           `json:"room"`
	ConfigHash    string           `json:"configHash"`
	Config        json.RawMessage  `json:"config"`
	Token         string           `json:"token"`
	URL           string           `json:"url"`
	ExpiresAt     time.Time        `json:"expiresAt"`
	ICEServers    []turn.ICEServer `json:"iceServers,omitempty"`
	EncryptionKey string           `json:"encryptionKey,omitempty"`

	AgentDispatchID string `json:"agentDispatchId,omitempty"`
}

func (h *harness) post(t *testing.T, body string) (int, []byte) {
	t.Helper()
	resp, err := h.server.Client().Post(h.server.URL+"/sessions", "application/json", strings.NewReader(body))
	if err != nil {
		t.Fatalf("post /sessions: %v", err)
	}
	defer closeBody(t, resp)
	raw, err := readAll(resp)
	if err != nil {
		t.Fatalf("read response: %v", err)
	}
	return resp.StatusCode, raw
}

func closeBody(t *testing.T, resp *http.Response) {
	t.Helper()
	if err := resp.Body.Close(); err != nil {
		t.Errorf("close response body: %v", err)
	}
}

func readAll(resp *http.Response) ([]byte, error) {
	var buf bytes.Buffer
	if _, err := buf.ReadFrom(resp.Body); err != nil {
		return nil, err
	}
	return buf.Bytes(), nil
}

func (h *harness) create(t *testing.T, body string) sessionResponse {
	t.Helper()
	status, raw := h.post(t, body)
	if status != http.StatusCreated {
		t.Fatalf("POST /sessions returned %d: %s", status, raw)
	}
	var out sessionResponse
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	return out
}

func request(language, channel string) string {
	return `{"tenantId":"` + tenantID + `","profile":"support","language":"` + language +
		`","channel":"` + channel + `"}`
}

func TestCreateSessionResolvesStoresAndMints(t *testing.T) {
	t.Parallel()
	h := serve(t)
	got := h.create(t, request("en-IN", "webrtc"))

	if got.Room != got.SessionID {
		t.Errorf("room %q does not match session %q", got.Room, got.SessionID)
	}
	if got.Token != "stub."+got.SessionID+"."+got.ParticipantID {
		t.Errorf("token was not minted for this session: %q", got.Token)
	}
	if h.transport.grant.Role != config.RoleParticipant {
		t.Errorf("minted for role %q, want the default participant", h.transport.grant.Role)
	}
	if h.transport.grant.TTL != 15*time.Minute {
		t.Errorf("token ttl is %s", h.transport.grant.TTL)
	}

	stored, err := h.store.Session(t.Context(), got.SessionID)
	if err != nil {
		t.Fatalf("session was not stored: %v", err)
	}
	if !bytes.Equal(stored.Config, got.Config) {
		t.Error("the stored document differs from the one returned to the caller")
	}
	if stored.ConfigHash != got.ConfigHash {
		t.Errorf("stored hash %q, returned %q", stored.ConfigHash, got.ConfigHash)
	}
	recomputed, err := config.HashDocument(stored.Config)
	if err != nil {
		t.Fatal(err)
	}
	if recomputed != stored.ConfigHash {
		t.Errorf("the stored document hashes to %s, not the stored %s", recomputed, stored.ConfigHash)
	}
}

func turnStrategy(t *testing.T, r sessionResponse) config.TurnStrategy {
	t.Helper()
	cfg, err := config.Parse(r.Config)
	if err != nil {
		t.Fatalf("the returned document is not a valid resolved config: %v", err)
	}
	return cfg.Turn.Strategy
}

func TestHindiAndEnglishResolveDifferentTurnStrategies(t *testing.T) {
	t.Parallel()
	h := serve(t)
	hindi := h.create(t, request("hi", "webrtc"))
	english := h.create(t, request("en-IN", "webrtc"))

	hi, en := turnStrategy(t, hindi), turnStrategy(t, english)
	if hi == en {
		t.Fatalf("both languages resolved %s from the same base", hi)
	}
	if hi != config.TurnProviderEndpointing {
		t.Errorf("hi resolved %s, want %s", hi, config.TurnProviderEndpointing)
	}
	if en != config.TurnSemantic {
		t.Errorf("en-IN resolved %s, want %s", en, config.TurnSemantic)
	}
	if hindi.ConfigHash == english.ConfigHash {
		t.Error("two different documents share one hash")
	}
}

func TestTheSameRequestResolvesToTheSameHash(t *testing.T) {
	t.Parallel()
	h := serve(t)
	first := h.create(t, request("hi", "telephony"))
	second := h.create(t, request("hi", "telephony"))

	if first.SessionID == second.SessionID {
		t.Fatal("two sessions were minted the same id")
	}
	if stripIdentity(t, first.Config) != stripIdentity(t, second.Config) {
		t.Fatal("the same request resolved to two different documents")
	}
}

func stripIdentity(t *testing.T, document json.RawMessage) string {
	t.Helper()
	var doc map[string]json.RawMessage
	if err := json.Unmarshal(document, &doc); err != nil {
		t.Fatal(err)
	}
	delete(doc, "sessionId")
	delete(doc, "configHash")
	raw, err := json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	hash, err := config.HashDocument(raw)
	if err != nil {
		t.Fatal(err)
	}
	return hash
}

func (h *harness) reject(t *testing.T, body string, wantStatus int) *errs.Error {
	t.Helper()
	status, raw := h.post(t, body)
	if status != wantStatus {
		t.Fatalf("POST /sessions returned %d, want %d: %s", status, wantStatus, raw)
	}
	if err := schema.ValidateDocument(schema.Error, raw, errs.CodeInternal); err != nil {
		t.Errorf("the error body does not satisfy the error schema: %v", err)
	}
	var out errs.Error
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("decode error body: %v", err)
	}
	return &out
}

func TestAnUnsupportedLanguageIsRejectedWithItsPointer(t *testing.T) {
	t.Parallel()
	de := serve(t).reject(t, request("cy", "webrtc"), http.StatusBadRequest)
	if de.Code != errs.CodeUnsupportedCapability {
		t.Errorf("want %s, got %s", errs.CodeUnsupportedCapability, de.Code)
	}
	if !strings.Contains(strings.Join(de.Details, "\n"), "/language") {
		t.Errorf("no detail points at /language: %v", de.Details)
	}
}

func TestABrokenOverrideIsRejectedWithEveryPointer(t *testing.T) {
	t.Parallel()
	body := `{"tenantId":"` + tenantID + `","language":"hi","channel":"telephony",
		"overrides":{"recording":{"enabled":true,"layout":"track","startAt":"session_create"}}}`
	de := serve(t).reject(t, body, http.StatusBadRequest)

	joined := strings.Join(de.Details, "\n")
	for _, pointer := range []string{"/recording/consentArtifactId", "/recording/layout"} {
		if !strings.Contains(joined, pointer) {
			t.Errorf("no detail points at %s; an operator fixes one rule per round trip\n%v", pointer, de.Details)
		}
	}
}

func TestAnUnknownRequestFieldIsRejected(t *testing.T) {
	t.Parallel()
	body := `{"tenantId":"` + tenantID + `","language":"hi","channel":"webrtc","role":"participant","admin":true}`
	if de := serve(t).reject(t, body, http.StatusBadRequest); de.Code != errs.CodeInvalidConfig {
		t.Errorf("want %s, got %s", errs.CodeInvalidConfig, de.Code)
	}
}

func TestNothingIsStoredWhenResolutionFails(t *testing.T) {
	t.Parallel()
	h := serve(t)
	if de := h.reject(t, request("cy", "webrtc"), http.StatusBadRequest); de.Code != errs.CodeUnsupportedCapability {
		t.Errorf("want %s, got %s", errs.CodeUnsupportedCapability, de.Code)
	}
	if h.transport.grant.Room != "" {
		t.Error("a token was minted for a session that was never resolved")
	}
}

func TestOnlyPostCreatesASession(t *testing.T) {
	t.Parallel()
	h := serve(t)
	resp, err := h.server.Client().Get(h.server.URL + "/sessions")
	if err != nil {
		t.Fatal(err)
	}
	defer closeBody(t, resp)
	if resp.StatusCode != http.StatusMethodNotAllowed {
		t.Errorf("GET /sessions returned %d, want %d", resp.StatusCode, http.StatusMethodNotAllowed)
	}
}
