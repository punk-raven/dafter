package control_test

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

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
