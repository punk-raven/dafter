package control_test

import (
	"bytes"
	"encoding/json"
	"net/http"
	"os"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

const scribeOverrides = `"transcription":{"mode":"live","consentArtifactId":"consent_tr"},
	"scribe":{"enabled":true,"consentArtifactId":"consent_sc"}`

func scribeRequest(privacyMode string) string {
	return `{"tenantId":"` + tenantID + `","language":"hi","channel":"webrtc",
		"overrides":{"privacyMode":"` + privacyMode + `",` + scribeOverrides + `}}`
}

type scribeCreated struct {
	sessionResponse
	AgentDispatchID  string `json:"agentDispatchId"`
	ScribeDispatchID string `json:"scribeDispatchId"`
}

func (h *harness) createScribe(t *testing.T, body string) scribeCreated {
	t.Helper()
	status, raw := h.post(t, body)
	if status != http.StatusCreated {
		t.Fatalf("POST /sessions returned %d: %s", status, raw)
	}
	var out scribeCreated
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func TestTheScribeIsDispatchedBesideTheAgentWithTheSameDocument(t *testing.T) {
	t.Parallel()
	h := serve(t)
	got := h.createScribe(t, scribeRequest("open"))
	if got.AgentDispatchID == "" || got.ScribeDispatchID == "" || got.AgentDispatchID == got.ScribeDispatchID {
		t.Fatalf("dispatches agent %q scribe %q, want two", got.AgentDispatchID, got.ScribeDispatchID)
	}
	d := h.transport.dispatched
	if len(d) != 2 || d[0].Pool != "dafter-py" || d[1].Pool != config.DefaultScribePool {
		t.Fatalf("dispatched %+v, want the agent's pool then the scribe's", d)
	}
	if !bytes.Equal(d[0].Metadata, d[1].Metadata) || d[1].Room != got.Room {
		t.Error("the scribe was handed another document than the agent; one session, one hashed config")
	}
	cfg, err := config.Parse(d[1].Metadata)
	if err != nil {
		t.Fatal(err)
	}
	if !cfg.ScribeEnabled() || cfg.Scribe.LLM == nil || cfg.Scribe.LLM.Provider != "sarvam" {
		t.Errorf("the catalog resolved scribe %+v, want it on with the default Sarvam llm", cfg.Scribe)
	}
}

func TestASessionWithoutAScribeDispatchesOnlyTheAgent(t *testing.T) {
	t.Parallel()
	h := serve(t)
	got := h.createScribe(t, request("hi", "webrtc"))
	if got.ScribeDispatchID != "" || len(h.transport.dispatched) != 1 {
		t.Errorf("a session with the scribe off dispatched %+v", h.transport.dispatched)
	}
}

func TestAScribeThatCannotBeDispatchedNeverFailsTheCall(t *testing.T) {
	t.Parallel()
	h := serve(t)
	h.transport.failPool = config.DefaultScribePool
	got := h.createScribe(t, scribeRequest("open"))
	if got.Token == "" || got.AgentDispatchID == "" {
		t.Fatalf("the call lost its token or agent when only the scribe failed: %+v", got)
	}
	if got.ScribeDispatchID != "" {
		t.Errorf("a failed scribe dispatch reported id %q", got.ScribeDispatchID)
	}
}

func TestASealedSessionRefusesAScribeAtCreate(t *testing.T) {
	t.Parallel()
	h := serve(t)
	body := `{"tenantId":"` + tenantID + `","language":"hi","channel":"webrtc",
		"overrides":{"privacyMode":"sealed","agent":{"enabled":false},"scribe":{"enabled":true,"consentArtifactId":"c"}}}`
	de := h.reject(t, body, http.StatusBadRequest)
	if de.Code != errs.CodePrivacyModeForbids || len(h.transport.dispatched) != 0 {
		t.Errorf("a sealed scribe was answered %s with dispatches %+v", de.Code, h.transport.dispatched)
	}
}

func TestTheScribeGetsATrustedAgentKeyTheWayTheAgentDoes(t *testing.T) {
	t.Parallel()
	h := serve(t)
	trusted := h.createScribe(t, scribeRequest("trusted_agent"))
	without := h.create(t, trustedAgentRequest("hi"))
	open := h.createScribe(t, scribeRequest("open"))
	path := func(id string) string { return "/sessions/" + id + "/scribe/key" }

	status, raw := h.as(t, workerSecret, http.MethodPost, path(trusted.SessionID), keyRequest(trusted.ConfigHash))
	if status != http.StatusOK {
		t.Fatalf("scribe key returned %d: %s", status, raw)
	}
	var key struct {
		EncryptionKey string `json:"encryptionKey"`
	}
	if err := json.Unmarshal(raw, &key); err != nil || key.EncryptionKey != trusted.EncryptionKey {
		t.Errorf("the scribe got key %q (%v), want the one the humans hold", key.EncryptionKey, err)
	}

	cases := []struct {
		name, credential, session, hash string
		status                          int
		pointer                         string
	}{
		{"no credential", "", trusted.SessionID, trusted.ConfigHash, http.StatusUnauthorized, ""},
		{"another document", workerSecret, trusted.SessionID, open.ConfigHash, http.StatusBadRequest, "/configHash"},
		{"a session without a scribe", workerSecret, without.SessionID, without.ConfigHash, http.StatusBadRequest, "/scribe/enabled"},
		{"an open session", workerSecret, open.SessionID, open.ConfigHash, http.StatusBadRequest, "/privacyMode"},
	}
	for _, c := range cases {
		status, raw := h.as(t, c.credential, http.MethodPost, path(c.session), keyRequest(c.hash))
		if status != c.status || !bytes.Contains(raw, []byte(c.pointer)) {
			t.Errorf("%s: returned %d %s, want %d at %s", c.name, status, raw, c.status, c.pointer)
		}
		if bytes.Contains(raw, []byte(trusted.EncryptionKey)) {
			t.Errorf("%s: the key leaked", c.name)
		}
	}
}

func TestAScribeRefusalIsReadBackBesideTheAgents(t *testing.T) {
	t.Parallel()
	h := serve(t)
	got := h.createScribe(t, scribeRequest("open"))
	path := "/sessions/" + got.SessionID + "/scribe/refusal"
	refusal := `{"code":"authentication_failed","message":"the scribe llm cannot run","retryable":false,"details":["at '/scribe/llm/credentialRef': SARVAM_API_KEY"]}`

	if status, _ := h.as(t, "", http.MethodPost, path, refusal); status != http.StatusUnauthorized {
		t.Errorf("an unauthenticated refusal returned %d", status)
	}
	if status, _ := h.as(t, workerSecret, http.MethodPost, path, `{"code":"internal","message":"m","retryable":false,"text":"x"}`); status != http.StatusBadRequest {
		t.Errorf("a refusal outside the error schema returned %d", status)
	}
	if status, raw := h.as(t, workerSecret, http.MethodPost, path, refusal); status != http.StatusNoContent {
		t.Fatalf("refusal returned %d %s", status, raw)
	}
	view := h.read(t, got.SessionID)
	var stored errs.Error
	if err := json.Unmarshal(view.ScribeRefusal, &stored); err != nil {
		t.Fatal(err)
	}
	if stored.Code != errs.CodeAuthenticationFailed || view.AgentRefusal != nil {
		t.Errorf("read back scribe refusal %+v and agent refusal %s", stored, view.AgentRefusal)
	}
}

const scribeJobOverrides = `{` + scribeOverrides + `}`

var scribeJobs = []struct {
	fixture string
	profile string
}{
	{"../../../testdata/scribe/hindi-scribe-job.json", ""},
	{"../../../testdata/scribe/hindi-scribe-gemini-job.json", "scribe-gemini"},
	{"../../../testdata/scribe/hindi-scribe-nvidia-job.json", "scribe-nvidia"},
}

func TestTheScribeJobsArePinnedForTheScribe(t *testing.T) {
	t.Parallel()
	catalog := embeddedCatalog(t)
	for _, job := range scribeJobs {
		resolved, err := catalog.Resolve(config.Request{
			SessionID: "s_7f3a9c21", TenantID: tenantID, Profile: job.profile, Language: "hi",
			Channel: config.ChannelWebRTC, Overrides: json.RawMessage(scribeJobOverrides),
		})
		if err != nil {
			t.Fatalf("%s: %v", job.fixture, err)
		}
		if os.Getenv("DAFTER_UPDATE_FIXTURES") == "1" {
			if err := os.WriteFile(job.fixture, append(resolved.Document, '\n'), 0o644); err != nil {
				t.Fatal(err)
			}
		}
		want, err := os.ReadFile(job.fixture)
		if err != nil {
			t.Fatal(err)
		}
		if !bytes.Equal(bytes.TrimSpace(want), resolved.Document) {
			t.Errorf("the scribe job changed; the scribe's tests read %s, so rerun with DAFTER_UPDATE_FIXTURES=1 and check both halves\n got: %s", job.fixture, resolved.Document)
		}
	}
}

func TestAProfileSwitchesTheScribesLLMWithoutCarryingSarvamFields(t *testing.T) {
	t.Parallel()
	catalog := embeddedCatalog(t)
	for profile, endpoint := range map[string]string{"scribe-gemini": "google", "scribe-nvidia": "nvidia"} {
		resolved, err := catalog.Resolve(config.Request{
			SessionID: "s_7f3a9c21", TenantID: tenantID, Profile: profile, Language: "hi",
			Channel: config.ChannelWebRTC, Overrides: json.RawMessage(scribeJobOverrides),
		})
		if err != nil {
			t.Fatalf("%s: %v", profile, err)
		}
		for name, ref := range map[string]*config.ProviderRef{"llm": resolved.Config.Scribe.LLM, "judge": resolved.Config.Scribe.Judge} {
			if ref.Provider != "openai_compat" || ref.Region != "" || ref.Options["endpoint"] != endpoint {
				t.Errorf("%s %s resolved to %+v", profile, name, ref)
			}
			if _, leaked := ref.Options["thinking"]; leaked {
				t.Errorf("%s %s carries a Sarvam-only option: %+v", profile, name, ref.Options)
			}
		}
	}
}
