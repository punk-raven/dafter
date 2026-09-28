package control_test

import (
	"bytes"
	"encoding/json"
	"net/http"
	"os"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

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

const semanticOverride = `{"turn": {"strategy": "semantic", "localVadEnabled": true}}`

var agentJobs = []struct {
	fixture   string
	language  string
	overrides string
}{
	{"../../../testdata/agent/hindi-webrtc-job.json", "hi", ""},
	{"../../../testdata/agent/hindi-semantic-webrtc-job.json", "hi", semanticOverride},
	{"../../../testdata/agent/english-webrtc-job.json", "en-IN", ""},
}

func embeddedCatalog(t *testing.T) *config.Catalog {
	t.Helper()
	raw, err := os.ReadFile(catalogPath)
	if err != nil {
		t.Fatal(err)
	}
	catalog, err := config.LoadCatalog(raw)
	if err != nil {
		t.Fatal(err)
	}
	return catalog
}

func TestTheAgentJobsArePinnedForTheWorker(t *testing.T) {
	t.Parallel()
	catalog := embeddedCatalog(t)
	for _, job := range agentJobs {
		resolved, err := catalog.Resolve(config.Request{
			SessionID: "s_7f3a9c21", TenantID: tenantID, Language: job.language, Channel: config.ChannelWebRTC,
			Overrides: json.RawMessage(job.overrides),
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
			t.Errorf("the agent job changed; the worker's tests read %s, so rerun with DAFTER_UPDATE_FIXTURES=1 and check both halves\n got: %s", job.fixture, resolved.Document)
		}
	}
}

func TestASessionOverrideSelectsTheTurnDetectorForHindi(t *testing.T) {
	t.Parallel()
	catalog := embeddedCatalog(t)
	for _, channel := range []config.Channel{config.ChannelWebRTC, config.ChannelTelephony} {
		resolved, err := catalog.Resolve(config.Request{
			SessionID: "s_7f3a9c21", TenantID: tenantID, Language: "hi", Channel: channel,
			Overrides: json.RawMessage(semanticOverride),
		})
		if err != nil {
			t.Fatalf("%s: %v", channel, err)
		}
		turn := resolved.Config.Turn
		if turn.Strategy != config.TurnSemantic || !turn.LocalVADDecidesTurn() {
			t.Errorf("%s: the override resolved to %s with a local VAD deciding it %v; an A/B cannot select the turn detector", channel, turn.Strategy, turn.LocalVADDecidesTurn())
		}
	}
}

func TestTheCatalogAgentIsNamedWaitsToBeCalledAndGreetsOnlyWhenASessionAsks(t *testing.T) {
	t.Parallel()
	catalog := embeddedCatalog(t)
	for _, tc := range []struct {
		overrides string
		greets    bool
	}{
		{"", false},
		{`{"agent":{"greets":true}}`, true},
	} {
		resolved, err := catalog.Resolve(config.Request{
			SessionID: "s_7f3a9c21", TenantID: tenantID, Language: "hi", Channel: config.ChannelWebRTC,
			Overrides: json.RawMessage(tc.overrides),
		})
		if err != nil {
			t.Fatal(err)
		}
		cfg, err := config.Parse(resolved.Document)
		if err != nil {
			t.Fatal(err)
		}
		if cfg.Agent.Name != "Nivya" || cfg.Agent.Greets == nil || *cfg.Agent.Greets != tc.greets {
			t.Errorf("overrides %q resolved agent name %q greets %v; want Nivya greeting %v", tc.overrides, cfg.Agent.Name, cfg.Agent.Greets, tc.greets)
		}
		if !cfg.Agent.Addressing.WaitsToBeCalled() || cfg.Agent.Addressing.Mode != config.AddressingTranscript {
			t.Errorf("overrides %q resolved addressing %+v; the catalog agent waits to be called by name", tc.overrides, cfg.Agent.Addressing)
		}
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
