package control_test

import (
	"bytes"
	"context"
	"encoding/base64"
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
	recalled    []string
	live        []string
	recallErr   error
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

func (h *harness) join(t *testing.T, sessionID, body string) (int, []byte) {
	t.Helper()
	resp, err := h.server.Client().Post(h.server.URL+"/sessions/"+sessionID+"/join", "application/json", strings.NewReader(body))
	if err != nil {
		t.Fatalf("post /sessions/%s/join: %v", sessionID, err)
	}
	defer closeBody(t, resp)
	raw, err := readAll(resp)
	if err != nil {
		t.Fatalf("read response: %v", err)
	}
	return resp.StatusCode, raw
}

func TestJoinMintsAFreshParticipantForTheStoredRoom(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, request("en-IN", "webrtc"))

	status, raw := h.join(t, created.SessionID, `{"role":"participant"}`)
	if status != http.StatusOK {
		t.Fatalf("POST /sessions/{id}/join returned %d: %s", status, raw)
	}
	var joined sessionResponse
	if err := json.Unmarshal(raw, &joined); err != nil {
		t.Fatalf("decode response: %v", err)
	}

	if joined.SessionID != created.SessionID || joined.Room != created.Room {
		t.Errorf("joined session %q room %q, want %q %q", joined.SessionID, joined.Room, created.SessionID, created.Room)
	}
	if joined.ParticipantID == "" || joined.ParticipantID == created.ParticipantID {
		t.Errorf("participant %q is not fresh (creator was %q)", joined.ParticipantID, created.ParticipantID)
	}
	if joined.Token != "stub."+created.Room+"."+joined.ParticipantID {
		t.Errorf("token was not minted for this room and participant: %q", joined.Token)
	}
	if h.transport.grant.Room != created.Room || h.transport.grant.Identity != joined.ParticipantID {
		t.Errorf("grant was for room %q identity %q", h.transport.grant.Room, h.transport.grant.Identity)
	}
	if joined.ConfigHash != created.ConfigHash || !bytes.Equal(joined.Config, created.Config) {
		t.Error("the joiner did not receive the stored session document")
	}
}

func TestJoinDefaultsToTheParticipantRole(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, request("en-IN", "webrtc"))
	if status, raw := h.join(t, created.SessionID, `{}`); status != http.StatusOK {
		t.Fatalf("POST /sessions/{id}/join returned %d: %s", status, raw)
	}
	if h.transport.grant.Role != config.RoleParticipant {
		t.Errorf("minted for role %q, want the default participant", h.transport.grant.Role)
	}
}

func TestJoinRejectsSessionsItCannotExplain(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, request("en-IN", "webrtc"))
	h.transport.grant = transport.Grant{}

	cases := []struct {
		name, sessionID, body string
	}{
		{"unknown session id", "s_00000000", `{}`},
		{"malformed session id", "not-a-session", `{}`},
		{"unknown request field", created.SessionID, `{"role":"participant","admin":true}`},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			status, raw := h.join(t, tc.sessionID, tc.body)
			if status != http.StatusBadRequest {
				t.Fatalf("returned %d, want %d: %s", status, http.StatusBadRequest, raw)
			}
			if err := schema.ValidateDocument(schema.Error, raw, errs.CodeInternal); err != nil {
				t.Errorf("the error body does not satisfy the error schema: %v", err)
			}
			var de errs.Error
			if err := json.Unmarshal(raw, &de); err != nil {
				t.Fatalf("decode error body: %v", err)
			}
			if de.Code != errs.CodeInvalidConfig {
				t.Errorf("want %s, got %s", errs.CodeInvalidConfig, de.Code)
			}
		})
	}
	if h.transport.grant.Room != "" {
		t.Error("a token was minted for a join that was rejected")
	}
}

func sealedRequest(language string) string {
	return `{"tenantId":"` + tenantID + `","language":"` + language + `","channel":"webrtc",
		"overrides":{"privacyMode":"sealed","agent":{"enabled":false}}}`
}

func trustedAgentRequest(language string) string {
	return `{"tenantId":"` + tenantID + `","language":"` + language + `","channel":"webrtc",
		"overrides":{"privacyMode":"trusted_agent"}}`
}

func (h *harness) joinAs(t *testing.T, sessionID string, role config.Role) (sessionResponse, []byte) {
	t.Helper()
	status, raw := h.join(t, sessionID, `{"role":"`+string(role)+`"}`)
	if status != http.StatusOK {
		t.Fatalf("POST /sessions/{id}/join as %s returned %d: %s", role, status, raw)
	}
	var out sessionResponse
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	return out, raw
}

func TestASealedSessionMintsOneKeyAndHandsItToTheHumans(t *testing.T) {
	t.Parallel()
	h := serve(t)
	status, raw := h.post(t, sealedRequest("en-IN"))
	if status != http.StatusCreated {
		t.Fatalf("POST /sessions returned %d: %s", status, raw)
	}
	var created sessionResponse
	if err := json.Unmarshal(raw, &created); err != nil {
		t.Fatal(err)
	}

	key := created.EncryptionKey
	if len(key) != 43 {
		t.Fatalf("key %q is not 256 bits of base64url", key)
	}
	if _, err := base64.RawURLEncoding.DecodeString(key); err != nil {
		t.Fatalf("key is not base64url: %v", err)
	}
	if bytes.Contains(created.Config, []byte(key)) {
		t.Error("the key is inside the resolved document, which is hashed, stored and shown to every joiner")
	}
	cfg, err := config.Parse(created.Config)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.EncryptionMode() != config.EncryptionE2EE || !cfg.MintsSharedKey() {
		t.Errorf("the sealed document does not state e2ee under server_shared: %+v", cfg.Media.Encryption)
	}

	stored, err := h.store.Session(t.Context(), created.SessionID)
	if err != nil {
		t.Fatal(err)
	}
	if stored.EncryptionKey != key {
		t.Errorf("stored key %q differs from the one handed out", stored.EncryptionKey)
	}
	if bytes.Contains(stored.Config, []byte(key)) {
		t.Error("the key leaked into the stored document")
	}

	for _, role := range []config.Role{config.RoleParticipant, config.RolePresenter, config.RoleObserver} {
		joined, _ := h.joinAs(t, created.SessionID, role)
		if joined.EncryptionKey != key {
			t.Errorf("%s joined with key %q, want the session's own key; two keys make one session two calls", role, joined.EncryptionKey)
		}
	}
	for _, role := range []config.Role{config.RoleAgent, config.RoleRecorder} {
		joined, rawJoin := h.joinAs(t, created.SessionID, role)
		if joined.EncryptionKey != "" {
			t.Errorf("%s was handed the key of a sealed session", role)
		}
		if bytes.Contains(rawJoin, []byte("encryptionKey")) || bytes.Contains(rawJoin, []byte(key)) {
			t.Errorf("the %s join response carries the key field at all: %s", role, rawJoin)
		}
		if joined.Token == "" {
			t.Errorf("%s was refused a token; the key is withheld, the room is not", role)
		}
	}

	second := h.create(t, sealedRequest("en-IN"))
	if second.EncryptionKey == key {
		t.Error("two sessions share one key")
	}
}

func TestATrustedAgentSessionDisclosesTheKeyToTheAgent(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, trustedAgentRequest("en-IN"))
	if created.EncryptionKey == "" {
		t.Fatal("a trusted_agent session minted no key")
	}
	cfg, err := config.Parse(created.Config)
	if err != nil {
		t.Fatal(err)
	}
	if !cfg.Agent.Enabled || cfg.EncryptionMode() != config.EncryptionE2EE {
		t.Errorf("trusted_agent resolved with agent %v and encryption %s", cfg.Agent.Enabled, cfg.EncryptionMode())
	}
	if agent, _ := h.joinAs(t, created.SessionID, config.RoleAgent); agent.EncryptionKey != created.EncryptionKey {
		t.Errorf("the agent joined with key %q; trusted_agent is the mode that discloses it", agent.EncryptionKey)
	}
	if recorder, _ := h.joinAs(t, created.SessionID, config.RoleRecorder); recorder.EncryptionKey != "" {
		t.Error("a recorder was handed the key; the egress sees ciphertext by design")
	}
}

func TestAnOpenSessionCarriesNoKey(t *testing.T) {
	t.Parallel()
	h := serve(t)
	status, raw := h.post(t, request("en-IN", "webrtc"))
	if status != http.StatusCreated {
		t.Fatalf("POST /sessions returned %d: %s", status, raw)
	}
	if bytes.Contains(raw, []byte("encryptionKey")) {
		t.Errorf("an open session's create response carries a key field: %s", raw)
	}
	var created sessionResponse
	if err := json.Unmarshal(raw, &created); err != nil {
		t.Fatal(err)
	}
	cfg, err := config.Parse(created.Config)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.EncryptionMode() != config.EncryptionTransport || cfg.Media.Encryption == nil {
		t.Errorf("an open session does not state transport encryption: %+v", cfg.Media.Encryption)
	}
	if stored, err := h.store.Session(t.Context(), created.SessionID); err != nil || stored.EncryptionKey != "" {
		t.Errorf("an open session was stored with key %q (%v)", stored.EncryptionKey, err)
	}
	for _, role := range config.AllRoles {
		if _, rawJoin := h.joinAs(t, created.SessionID, role); bytes.Contains(rawJoin, []byte("encryptionKey")) {
			t.Errorf("an open session's join response as %s carries a key field: %s", role, rawJoin)
		}
	}
}

func TestRecordingInASealedSessionIsRefusedAtCreate(t *testing.T) {
	t.Parallel()
	body := `{"tenantId":"` + tenantID + `","language":"en-IN","channel":"webrtc",
		"overrides":{"privacyMode":"sealed","agent":{"enabled":false},
		"recording":{"enabled":true,"layout":"track","consentArtifactId":"consent_1"}}}`
	h := serve(t)
	de := h.reject(t, body, http.StatusBadRequest)
	if de.Code != errs.CodePrivacyModeForbids {
		t.Errorf("want %s, got %s", errs.CodePrivacyModeForbids, de.Code)
	}
	if !strings.Contains(strings.Join(de.Details, "\n"), "/recording/enabled") {
		t.Errorf("no detail points at /recording/enabled: %v", de.Details)
	}
	if h.transport.grant.Room != "" {
		t.Error("a token was minted for a session that was refused")
	}
}

func TestNoICEServersWhenTURNNotConfigured(t *testing.T) {
	t.Parallel()
	h := serve(t)
	got := h.create(t, request("en-IN", "webrtc"))
	if got.ICEServers != nil {
		t.Errorf("expected no iceServers, got %v", got.ICEServers)
	}
}

func serveTURN(t *testing.T, turnServer *httptest.Server) *harness {
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
	turnFetcher := turn.NewFetcherWithClient("test-id", "test-token", turnServer.Client())
	turnFetcher.SetBaseURL(turnServer.URL)
	svc := &control.Service{
		Catalog: catalog, Store: store, Transport: tport,
		TURN: turnFetcher, TokenTTL: 15 * time.Minute,
	}
	server := httptest.NewServer(svc.Handler())
	t.Cleanup(server.Close)
	return &harness{server: server, store: store, transport: tport}
}

func TestICEServersReturnedWhenTURNConfigured(t *testing.T) {
	t.Parallel()

	turnServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"iceServers":[{"urls":["turn:turn.example.com:3478"],"username":"u","credential":"c"}]}`))
	}))
	t.Cleanup(turnServer.Close)

	h := serveTURN(t, turnServer)
	got := h.create(t, request("en-IN", "webrtc"))
	if len(got.ICEServers) != 1 {
		t.Fatalf("want 1 ice server, got %d", len(got.ICEServers))
	}
	if got.ICEServers[0].Username != "u" {
		t.Errorf("username = %q", got.ICEServers[0].Username)
	}
}

func TestSessionCreatedEvenWhenTURNFails(t *testing.T) {
	t.Parallel()

	turnServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"error":"service down"}`))
	}))
	t.Cleanup(turnServer.Close)

	h := serveTURN(t, turnServer)
	got := h.create(t, request("en-IN", "webrtc"))
	if got.SessionID == "" {
		t.Fatal("session was not created")
	}
	if got.ICEServers != nil {
		t.Errorf("expected no iceServers on TURN failure, got %v", got.ICEServers)
	}
}

type recordingView struct {
	EgressID  string     `json:"egressId"`
	Layout    string     `json:"layout"`
	Status    string     `json:"status,omitempty"`
	StartedAt time.Time  `json:"startedAt"`
	StoppedAt *time.Time `json:"stoppedAt,omitempty"`
}

type recordingResponse struct {
	SessionID  string          `json:"sessionId"`
	Recordings []recordingView `json:"recordings"`
}

type sessionView struct {
	SessionID  string          `json:"sessionId"`
	Room       string          `json:"room"`
	ConfigHash string          `json:"configHash"`
	Config     json.RawMessage `json:"config"`
	Recordings []recordingView `json:"recordings"`

	AgentRefusal json.RawMessage `json:"agentRefusal"`
}

func recordingRequest(layout, startAt string) string {
	rec := `{"enabled":true,"layout":"` + layout + `","consentArtifactId":"consent_1"`
	if startAt != "" {
		rec += `,"startAt":"` + startAt + `"`
	}
	rec += `}`
	return `{"tenantId":"` + tenantID + `","language":"en-IN","channel":"webrtc","overrides":{"recording":` + rec + `}}`
}

func (h *harness) call(t *testing.T, method, path, body string) (int, []byte) {
	t.Helper()
	req, err := http.NewRequestWithContext(t.Context(), method, h.server.URL+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := h.server.Client().Do(req)
	if err != nil {
		t.Fatalf("%s %s: %v", method, path, err)
	}
	defer closeBody(t, resp)
	raw, err := readAll(resp)
	if err != nil {
		t.Fatalf("read response: %v", err)
	}
	return resp.StatusCode, raw
}

func (h *harness) recording(t *testing.T, action, sessionID, body string, wantStatus int) recordingResponse {
	t.Helper()
	status, raw := h.call(t, http.MethodPost, "/sessions/"+sessionID+"/recording/"+action, body)
	if status != wantStatus {
		t.Fatalf("POST /sessions/{id}/recording/%s returned %d, want %d: %s", action, status, wantStatus, raw)
	}
	var out recordingResponse
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	return out
}

func (h *harness) rejectRecording(t *testing.T, action, sessionID, body string) *errs.Error {
	t.Helper()
	status, raw := h.call(t, http.MethodPost, "/sessions/"+sessionID+"/recording/"+action, body)
	if status != http.StatusBadRequest {
		t.Fatalf("POST /sessions/{id}/recording/%s returned %d, want %d: %s", action, status, http.StatusBadRequest, raw)
	}
	if err := schema.ValidateDocument(schema.Error, raw, errs.CodeInternal); err != nil {
		t.Errorf("the error body does not satisfy the error schema: %v", err)
	}
	var de errs.Error
	if err := json.Unmarshal(raw, &de); err != nil {
		t.Fatalf("decode error body: %v", err)
	}
	return &de
}

func (h *harness) read(t *testing.T, sessionID string) sessionView {
	t.Helper()
	status, raw := h.call(t, http.MethodGet, "/sessions/"+sessionID, "")
	if status != http.StatusOK {
		t.Fatalf("GET /sessions/{id} returned %d: %s", status, raw)
	}
	var out sessionView
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	return out
}

func TestStartRecordsTheRoomCompositeFromTheStoredConfig(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, recordingRequest("room_composite", ""))

	out := h.recording(t, "start", created.SessionID, "", http.StatusCreated)
	if len(out.Recordings) != 1 || out.Recordings[0].EgressID != "EG_stub1" || out.Recordings[0].Status != "EGRESS_STARTING" {
		t.Fatalf("start answered %+v", out)
	}
	if out.Recordings[0].Layout != "room_composite" || out.Recordings[0].StartedAt.IsZero() {
		t.Errorf("recording view = %+v", out.Recordings[0])
	}

	started, _ := h.transport.egresses()
	if len(started) != 1 {
		t.Fatalf("%d egresses started", len(started))
	}
	req := started[0]
	if req.Room != created.Room || req.SessionID != created.SessionID || req.Layout != config.LayoutRoomComposite {
		t.Errorf("egress request = %+v", req)
	}
	if req.AudioOnly {
		t.Error("a webrtc session was recorded audio-only")
	}
	if req.Encoding == nil || req.Encoding.Width != 1280 || req.Encoding.Height != 720 ||
		req.Encoding.Framerate != 30 || req.Encoding.VideoBitrate != 3000 || req.Encoding.AudioBitrate != 128 ||
		req.Encoding.VideoCodec != config.EgressCodecH264Main {
		t.Errorf("the encode did not come from the shipped catalog: %+v", req.Encoding)
	}

	view := h.read(t, created.SessionID)
	if len(view.Recordings) != 1 || view.Recordings[0].EgressID != "EG_stub1" || view.Recordings[0].StoppedAt != nil {
		t.Errorf("session read shows %+v; the egress id and start time belong on the session", view.Recordings)
	}
	if view.ConfigHash != created.ConfigHash || !bytes.Equal(view.Config, created.Config) {
		t.Error("the session read does not return the stored document")
	}
}

func TestStartTakesTrackIDsForTheTrackLayouts(t *testing.T) {
	t.Parallel()
	h := serve(t)

	composite := h.create(t, recordingRequest("track_composite", ""))
	h.recording(t, "start", composite.SessionID, `{"audioTrackId":"TR_a1","videoTrackId":"TR_v1"}`, http.StatusCreated)

	track := h.create(t, recordingRequest("track", ""))
	h.recording(t, "start", track.SessionID, `{"trackId":"TR_a2"}`, http.StatusCreated)

	started, _ := h.transport.egresses()
	if len(started) != 2 {
		t.Fatalf("%d egresses started", len(started))
	}
	if started[0].Layout != config.LayoutTrackComposite || started[0].AudioTrackID != "TR_a1" || started[0].VideoTrackID != "TR_v1" {
		t.Errorf("track composite request = %+v", started[0])
	}
	if started[1].Layout != config.LayoutTrack || started[1].TrackID != "TR_a2" {
		t.Errorf("track request = %+v", started[1])
	}
	if started[0].CreateRoom || started[1].CreateRoom {
		t.Error("an on-demand start asked for the room to be created; a closed room would be recorded empty")
	}

	cases := []struct {
		name, sessionID, body, pointer string
	}{
		{"track composite without tracks", composite.SessionID, `{}`, "/audioTrackId"},
		{"track without a track", track.SessionID, ``, "/trackId"},
		{"track id on a composite", composite.SessionID, `{"trackId":"TR_a2"}`, "/trackId"},
		{"a track id the server could not have minted", track.SessionID, `{"trackId":"jane@example.com"}`, "/trackId"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			de := h.rejectRecording(t, "start", tc.sessionID, tc.body)
			if !strings.Contains(strings.Join(de.Details, "\n"), tc.pointer) {
				t.Errorf("no detail points at %s: %v", tc.pointer, de.Details)
			}
		})
	}
	if again, _ := h.transport.egresses(); len(again) != 2 {
		t.Errorf("a rejected start reached the media server: %d egresses", len(again))
	}
}

func TestRecordingIsRefusedWhereTheStoredConfigForbidsIt(t *testing.T) {
	t.Parallel()
	h := serve(t)
	plain := h.create(t, request("en-IN", "webrtc"))

	de := h.rejectRecording(t, "start", plain.SessionID, "")
	if de.Code != errs.CodeInvalidConfig || !strings.Contains(strings.Join(de.Details, "\n"), "/recording/enabled") {
		t.Errorf("a session that resolved without recording was not refused at /recording/enabled: %v", de)
	}
	if de = h.rejectRecording(t, "stop", plain.SessionID, ""); de.Code != errs.CodeInvalidConfig {
		t.Errorf("stop on an unrecorded session: %v", de)
	}
	if de = h.rejectRecording(t, "start", "s_00000000", ""); de.Code != errs.CodeInvalidConfig {
		t.Errorf("start on an unknown session: %v", de)
	}
	if de = h.rejectRecording(t, "start", plain.SessionID, `{"admin":true}`); de.Code != errs.CodeInvalidConfig {
		t.Errorf("an unknown request field was accepted: %v", de)
	}
	if started, _ := h.transport.egresses(); len(started) != 0 {
		t.Errorf("%d egresses started for refused requests", len(started))
	}
}

func TestStopEndsTheRunningRecordingsAndTheSessionReadShowsIt(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, recordingRequest("track", ""))
	h.recording(t, "start", created.SessionID, `{"trackId":"TR_a1"}`, http.StatusCreated)
	h.recording(t, "start", created.SessionID, `{"trackId":"TR_v1"}`, http.StatusCreated)

	one := h.recording(t, "stop", created.SessionID, `{"egressId":"EG_stub1"}`, http.StatusOK)
	if len(one.Recordings) != 1 || one.Recordings[0].EgressID != "EG_stub1" || one.Recordings[0].StoppedAt == nil {
		t.Fatalf("stop by id answered %+v", one)
	}
	if one.Recordings[0].Status != "EGRESS_ENDING" {
		t.Errorf("status %q is not what the media server said", one.Recordings[0].Status)
	}

	rest := h.recording(t, "stop", created.SessionID, "", http.StatusOK)
	if len(rest.Recordings) != 1 || rest.Recordings[0].EgressID != "EG_stub2" {
		t.Fatalf("stop without an id should end what is still running: %+v", rest)
	}
	if _, stopped := h.transport.egresses(); len(stopped) != 2 || stopped[0] != "EG_stub1" || stopped[1] != "EG_stub2" {
		t.Errorf("media server was told to stop %v", stopped)
	}

	de := h.rejectRecording(t, "stop", created.SessionID, "")
	if !strings.Contains(strings.Join(de.Details, "\n"), "/egressId") {
		t.Errorf("stopping with nothing running: %v", de)
	}
	if de = h.rejectRecording(t, "stop", created.SessionID, `{"egressId":"not-an-egress"}`); !strings.Contains(strings.Join(de.Details, "\n"), "/egressId") {
		t.Errorf("a malformed egress id: %v", de)
	}

	view := h.read(t, created.SessionID)
	if len(view.Recordings) != 2 {
		t.Fatalf("session read shows %d recordings", len(view.Recordings))
	}
	for _, rec := range view.Recordings {
		if rec.StoppedAt == nil || !rec.StoppedAt.After(rec.StartedAt) {
			t.Errorf("recording %s reads back as %+v after a stop", rec.EgressID, rec)
		}
	}
}

func TestSessionCreateStartsTheRoomCompositeBeforeATokenExists(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, recordingRequest("room_composite", "session_create"))

	started, _ := h.transport.egresses()
	if len(started) != 1 || started[0].Room != created.Room || started[0].Layout != config.LayoutRoomComposite {
		t.Fatalf("session_create did not start a room composite: %+v", started)
	}
	if !started[0].CreateRoom {
		t.Error("the room was not created first; the media server refuses an egress on a room nobody has joined")
	}
	view := h.read(t, created.SessionID)
	if len(view.Recordings) != 1 || view.Recordings[0].EgressID != "EG_stub1" {
		t.Errorf("the automatic recording is not on the session: %+v", view.Recordings)
	}

	later := h.create(t, recordingRequest("room_composite", "first_publish"))
	if again, _ := h.transport.egresses(); len(again) != 1 {
		t.Errorf("first_publish started an egress at create: %+v", again)
	}
	if view := h.read(t, later.SessionID); len(view.Recordings) != 0 {
		t.Errorf("a first_publish session shows recordings at create: %+v", view.Recordings)
	}

	h.transport.egressErr = errs.Errorf(errs.CodeProviderUnavailable, "no egress available")
	h.transport.grant = transport.Grant{}
	status, raw := h.post(t, recordingRequest("room_composite", "session_create"))
	if status != http.StatusServiceUnavailable {
		t.Fatalf("a create whose evidence-grade recording could not start returned %d: %s", status, raw)
	}
	if h.transport.grant.Room != "" {
		t.Error("a token was minted for a session whose recording never started")
	}
}

func TestReadingAnUnknownSessionFails(t *testing.T) {
	t.Parallel()
	h := serve(t)
	for _, id := range []string{"s_00000000", "not-a-session"} {
		if status, _ := h.call(t, http.MethodGet, "/sessions/"+id, ""); status != http.StatusBadRequest {
			t.Errorf("GET /sessions/%s returned %d", id, status)
		}
	}
}

func TestAnAgentSessionHandsTheStoredDocumentToItsPool(t *testing.T) {
	t.Parallel()
	h := serve(t)
	got := h.create(t, request("hi", "webrtc"))

	if len(h.transport.dispatched) != 1 || got.AgentDispatchID != "AD_stub1" {
		t.Fatalf("dispatches %+v, response id %q; one agent session is one dispatch", h.transport.dispatched, got.AgentDispatchID)
	}
	d := h.transport.dispatched[0]
	stored, err := h.store.Session(t.Context(), got.SessionID)
	if err != nil {
		t.Fatal(err)
	}
	if d.Room != got.Room || d.Pool != "dafter-py" || !bytes.Equal(d.Metadata, stored.Config) {
		t.Errorf("dispatched room %q pool %q; the worker must receive exactly the stored, hashed document", d.Room, d.Pool)
	}
}

const agentJobFixture = "../../../testdata/agent/hindi-webrtc-job.json"

func TestTheHindiAgentJobIsPinnedForTheWorker(t *testing.T) {
	t.Parallel()
	raw, err := os.ReadFile(catalogPath)
	if err != nil {
		t.Fatal(err)
	}
	catalog, err := config.LoadCatalog(raw)
	if err != nil {
		t.Fatal(err)
	}
	resolved, err := catalog.Resolve(config.Request{
		SessionID: "s_7f3a9c21", TenantID: tenantID, Language: "hi", Channel: config.ChannelWebRTC,
	})
	if err != nil {
		t.Fatal(err)
	}
	if os.Getenv("DAFTER_UPDATE_FIXTURES") == "1" {
		if err := os.WriteFile(agentJobFixture, append(resolved.Document, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	want, err := os.ReadFile(agentJobFixture)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(bytes.TrimSpace(want), resolved.Document) {
		t.Errorf("the Hindi agent job changed; the worker's tests read %s, so rerun with DAFTER_UPDATE_FIXTURES=1 and check both halves\n got: %s", agentJobFixture, resolved.Document)
	}
}

func TestNoAgentMeansNoDispatch(t *testing.T) {
	t.Parallel()
	h := serve(t)
	got := h.create(t, `{"tenantId":"`+tenantID+`","language":"hi","channel":"webrtc","overrides":{"agent":{"enabled":false}}}`)
	if len(h.transport.dispatched) != 0 || got.AgentDispatchID != "" {
		t.Errorf("a session without an agent dispatched one: %+v", h.transport.dispatched)
	}
}

func TestAFailedDispatchMintsNoToken(t *testing.T) {
	t.Parallel()
	h := serve(t)
	h.transport.dispatchErr = errs.Errorf(errs.CodeProviderUnavailable, "media server unreachable")
	status, raw := h.post(t, request("hi", "webrtc"))
	if status != http.StatusServiceUnavailable {
		t.Errorf("status %d: %s", status, raw)
	}
	if h.transport.grant.Identity != "" {
		t.Error("a token was minted for a session whose agent never got the job")
	}
}

type agentReply struct {
	SessionID       string   `json:"sessionId"`
	AgentDispatchID string   `json:"agentDispatchId"`
	Recalled        []string `json:"recalled"`
	Code            string   `json:"code"`
	Details         []string `json:"details"`
}

func (h *harness) agent(t *testing.T, sessionID, action, body string) (int, agentReply) {
	t.Helper()
	status, raw := h.call(t, http.MethodPost, "/sessions/"+sessionID+"/agent/"+action, body)
	var out agentReply
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("decode agent %s reply: %v: %s", action, err, raw)
	}
	return status, out
}

func TestInvitingTheAgentMidCallReplacesItWithOneDispatchOfTheStoredDocument(t *testing.T) {
	t.Parallel()
	h := serve(t)
	got := h.create(t, request("hi", "webrtc"))
	stored, err := h.store.Session(t.Context(), got.SessionID)
	if err != nil {
		t.Fatal(err)
	}

	status, reply := h.agent(t, got.SessionID, "start", "")
	if status != http.StatusCreated || reply.AgentDispatchID != "AD_stub2" {
		t.Fatalf("invite returned %d %+v", status, reply)
	}
	if len(reply.Recalled) != 1 || reply.Recalled[0] != got.AgentDispatchID {
		t.Errorf("recalled %v, want the dispatch made at create so one agent is in the room", reply.Recalled)
	}
	if len(h.transport.dispatched) != 2 {
		t.Fatalf("dispatches %+v", h.transport.dispatched)
	}
	d := h.transport.dispatched[1]
	if d.Room != got.Room || d.Pool != "dafter-py" || !bytes.Equal(d.Metadata, stored.Config) {
		t.Errorf("invited room %q pool %q; the worker must receive exactly the stored, hashed document", d.Room, d.Pool)
	}
}

func TestRemovingTheAgentRecallsEveryDispatchAndIsIdempotent(t *testing.T) {
	t.Parallel()
	h := serve(t)
	got := h.create(t, request("hi", "webrtc"))

	status, reply := h.agent(t, got.SessionID, "stop", "")
	if status != http.StatusOK || len(reply.Recalled) != 1 || reply.Recalled[0] != got.AgentDispatchID {
		t.Fatalf("remove returned %d %+v", status, reply)
	}
	status, reply = h.agent(t, got.SessionID, "stop", "")
	if status != http.StatusOK || len(reply.Recalled) != 0 {
		t.Errorf("a second remove returned %d %+v", status, reply)
	}
	if len(h.transport.dispatched) != 1 {
		t.Errorf("a remove dispatched: %+v", h.transport.dispatched)
	}
}

func TestTheStoredConfigDecidesWhetherAnAgentMayBeInvited(t *testing.T) {
	t.Parallel()
	h := serve(t)
	off := h.create(t, `{"tenantId":"`+tenantID+`","language":"hi","channel":"webrtc","overrides":{"agent":{"enabled":false}}}`)
	sealed := h.create(t, sealedRequest("hi"))

	cases := []struct {
		name, session, body, code, pointer string
	}{
		{"agent off", off.SessionID, "", string(errs.CodeInvalidConfig), "/agent/enabled"},
		{"sealed", sealed.SessionID, "", string(errs.CodePrivacyModeForbids), "/privacyMode"},
		{"request asks for its own pool", off.SessionID, `{"pool":"other"}`, string(errs.CodeInvalidConfig), ""},
	}
	for _, c := range cases {
		status, reply := h.agent(t, c.session, "start", c.body)
		if status != http.StatusBadRequest || reply.Code != c.code {
			t.Errorf("%s: invite returned %d %+v", c.name, status, reply)
		}
		if c.pointer != "" && (len(reply.Details) != 1 || !strings.Contains(reply.Details[0], c.pointer)) {
			t.Errorf("%s: details %v do not locate %s", c.name, reply.Details, c.pointer)
		}
	}
	if status, reply := h.agent(t, sealed.SessionID, "stop", ""); status != http.StatusBadRequest || reply.Code != string(errs.CodePrivacyModeForbids) {
		t.Errorf("remove on a sealed session returned %d %+v", status, reply)
	}
	if len(h.transport.dispatched) != 0 || len(h.transport.recalled) != 0 {
		t.Errorf("a refused request reached the media server: dispatched %+v recalled %v", h.transport.dispatched, h.transport.recalled)
	}
	if status, _ := h.call(t, http.MethodPost, "/sessions/s_00000000/agent/start", ""); status != http.StatusBadRequest {
		t.Errorf("inviting into an unknown session returned %d", status)
	}
}

func TestAFailedRecallDispatchesNoSecondAgent(t *testing.T) {
	t.Parallel()
	h := serve(t)
	got := h.create(t, request("hi", "webrtc"))
	h.transport.recallErr = errs.Errorf(errs.CodeProviderUnavailable, "media server unreachable")
	if status, reply := h.agent(t, got.SessionID, "start", ""); status != http.StatusServiceUnavailable {
		t.Errorf("invite returned %d %+v", status, reply)
	}
	if len(h.transport.dispatched) != 1 {
		t.Errorf("an invite whose recall failed dispatched anyway: %+v", h.transport.dispatched)
	}
}

func (h *harness) worker(t *testing.T, credential, sessionID, action, body string) (int, []byte) {
	t.Helper()
	req, err := http.NewRequestWithContext(t.Context(), http.MethodPost,
		h.server.URL+"/sessions/"+sessionID+"/agent/"+action, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Content-Type", "application/json")
	if credential != "" {
		req.Header.Set("Authorization", "Bearer "+credential)
	}
	resp, err := h.server.Client().Do(req)
	if err != nil {
		t.Fatalf("POST agent/%s: %v", action, err)
	}
	defer closeBody(t, resp)
	raw, err := readAll(resp)
	if err != nil {
		t.Fatal(err)
	}
	return resp.StatusCode, raw
}

func keyRequest(hash string) string {
	return `{"configHash":"` + hash + `"}`
}

func TestTheWorkerFetchesATrustedAgentSessionKeyOverItsOwnCredential(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, trustedAgentRequest("hi"))
	status, raw := h.worker(t, workerSecret, created.SessionID, "key", keyRequest(created.ConfigHash))
	if status != http.StatusOK {
		t.Fatalf("agent key returned %d: %s", status, raw)
	}
	var got struct {
		SessionID     string `json:"sessionId"`
		EncryptionKey string `json:"encryptionKey"`
	}
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if got.SessionID != created.SessionID || got.EncryptionKey != created.EncryptionKey {
		t.Errorf("the worker got %+v, want the one key the humans hold; two keys make one session two calls", got)
	}
	for _, d := range h.transport.dispatched {
		if bytes.Contains(d.Metadata, []byte(created.EncryptionKey)) {
			t.Error("the key rode the dispatch, which the media server reads")
		}
	}
}

func TestTheSessionKeyIsWithheldUnlessTheWorkerAndTheModeAllowIt(t *testing.T) {
	t.Parallel()
	h := serve(t)
	trusted := h.create(t, trustedAgentRequest("hi"))
	sealed := h.create(t, sealedRequest("hi"))
	open := h.create(t, request("hi", "webrtc"))

	cases := []struct {
		name, credential, session, body string
		status                          int
		code                            errs.ErrorCode
		pointer                         string
	}{
		{"no credential", "", trusted.SessionID, keyRequest(trusted.ConfigHash), http.StatusUnauthorized, errs.CodeAuthenticationFailed, ""},
		{"a client token", trusted.Token, trusted.SessionID, keyRequest(trusted.ConfigHash), http.StatusUnauthorized, errs.CodeAuthenticationFailed, ""},
		{"another document", workerSecret, trusted.SessionID, keyRequest(open.ConfigHash), http.StatusBadRequest, errs.CodeInvalidConfig, "/configHash"},
		{"a field the call does not take", workerSecret, trusted.SessionID, `{"configHash":"` + trusted.ConfigHash + `","role":"participant"}`, http.StatusBadRequest, errs.CodeInvalidConfig, ""},
		{"sealed", workerSecret, sealed.SessionID, keyRequest(sealed.ConfigHash), http.StatusBadRequest, errs.CodeInvalidConfig, "/agent/enabled"},
		{"open", workerSecret, open.SessionID, keyRequest(open.ConfigHash), http.StatusBadRequest, errs.CodePrivacyModeForbids, "/privacyMode"},
	}
	for _, c := range cases {
		status, raw := h.worker(t, c.credential, c.session, "key", c.body)
		var de errs.Error
		if err := json.Unmarshal(raw, &de); err != nil {
			t.Fatalf("%s: %v: %s", c.name, err, raw)
		}
		if status != c.status || de.Code != c.code {
			t.Errorf("%s: returned %d %s, want %d %s", c.name, status, de.Code, c.status, c.code)
		}
		if c.pointer != "" && (len(de.Details) != 1 || !strings.Contains(de.Details[0], c.pointer)) {
			t.Errorf("%s: details %v do not locate %s", c.name, de.Details, c.pointer)
		}
		for _, key := range []string{trusted.EncryptionKey, sealed.EncryptionKey} {
			if bytes.Contains(raw, []byte(key)) {
				t.Errorf("%s: a refused call carried a session key", c.name)
			}
		}
	}

	h.svc.WorkerSecret = ""
	if status, _ := h.worker(t, "", trusted.SessionID, "key", keyRequest(trusted.ConfigHash)); status != http.StatusUnauthorized {
		t.Errorf("with no worker credential configured an empty one was accepted: %d", status)
	}
}

func TestAWorkerRefusalIsReadBackUntilTheNextInvite(t *testing.T) {
	t.Parallel()
	h := serve(t)
	got := h.create(t, request("hi", "webrtc"))
	refusal := `{"code":"unsupported_capability","message":"this worker cannot run the session's turn strategy","retryable":false,"details":["at '/turn/strategy': semantic"]}`

	for name, body := range map[string]string{
		"a field the error schema does not have": `{"code":"internal","message":"m","retryable":false,"transcript":"x"}`,
		"a code outside the taxonomy":            `{"code":"worker_sad","message":"m","retryable":false}`,
		"not json":                               `refused`,
	} {
		if status, raw := h.worker(t, workerSecret, got.SessionID, "refusal", body); status != http.StatusBadRequest {
			t.Errorf("%s: returned %d %s", name, status, raw)
		}
	}
	if status, _ := h.worker(t, "", got.SessionID, "refusal", refusal); status != http.StatusUnauthorized {
		t.Errorf("an unauthenticated refusal returned %d", status)
	}
	if h.read(t, got.SessionID).AgentRefusal != nil {
		t.Fatal("a rejected report was stored")
	}

	if status, raw := h.worker(t, workerSecret, got.SessionID, "refusal", refusal); status != http.StatusNoContent {
		t.Fatalf("refusal returned %d %s", status, raw)
	}
	var stored errs.Error
	if err := json.Unmarshal(h.read(t, got.SessionID).AgentRefusal, &stored); err != nil {
		t.Fatal(err)
	}
	if stored.Code != errs.CodeUnsupportedCapability || len(stored.Details) != 1 || !strings.Contains(stored.Details[0], "/turn/strategy") {
		t.Errorf("the session reads back refusal %+v", stored)
	}

	if status, reply := h.agent(t, got.SessionID, "start", ""); status != http.StatusCreated {
		t.Fatalf("invite returned %d %+v", status, reply)
	}
	if raw := h.read(t, got.SessionID).AgentRefusal; raw != nil {
		t.Errorf("a fresh invite still shows the last refusal %s", raw)
	}
}
