package control_test

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"os"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/ids"
	"github.com/punk-raven/dafter/go/internal/transport"
)

const (
	callee     = "+12025550143"
	calleeTail = "2025550143"
)

var carrierTrunk = transport.Trunk{
	Provider: "carrier", Address: "sip.carrier.example", Transport: "udp",
	Numbers: []string{"+12025550100"}, AuthUsername: "user", AuthPassword: "pass",
}

func (s *stubTransport) PlaceCall(_ context.Context, c transport.PhoneCall) (transport.CallInfo, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.callErr != nil {
		return transport.CallInfo{}, s.callErr
	}
	s.calls = append(s.calls, c)
	if s.people == nil {
		s.people = map[string]string{}
	}
	s.people[c.Identity] = c.Room
	return transport.CallInfo{ParticipantID: "PA_stub", Identity: c.Identity, Room: c.Room, CallID: "SCL_stub"}, nil
}

func (s *stubTransport) HangUp(_ context.Context, room, identity string) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	placedIn, placed := s.people[identity]
	switch {
	case !placed || placedIn != room:
		return transport.ErrNoSuchParticipant
	case !slices.ContainsFunc(s.calls, func(c transport.PhoneCall) bool { return c.Identity == identity }):
		return transport.ErrNotAPhone
	}
	delete(s.people, identity)
	s.hungUp = append(s.hungUp, room+"/"+identity)
	return nil
}

type lockedBuffer struct {
	mu  sync.Mutex
	buf bytes.Buffer
}

func (b *lockedBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.Write(p)
}

func (b *lockedBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.buf.String()
}

func servePhone(t *testing.T) (*harness, *lockedBuffer) {
	t.Helper()
	h := serve(t)
	raw, err := os.ReadFile(catalogPath)
	if err != nil {
		t.Fatal(err)
	}
	var doc map[string]json.RawMessage
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	var tenants map[string]json.RawMessage
	if err := json.Unmarshal(doc["tenants"], &tenants); err != nil {
		t.Fatal(err)
	}
	var tenant map[string]any
	if err := json.Unmarshal(tenants[tenantID], &tenant); err != nil {
		t.Fatal(err)
	}
	for id, telephony := range map[string]any{
		tenantID:           map[string]any{"trunk": "carrier-out", "ringingTimeoutSeconds": 20},
		tenantElsewhere:    map[string]any{"trunk": "not-in-the-table"},
		tenantWithoutPhone: nil,
	} {
		tenant["telephony"] = telephony
		if telephony == nil {
			delete(tenant, "telephony")
		}
		if tenants[id], err = json.Marshal(tenant); err != nil {
			t.Fatal(err)
		}
	}
	if doc["tenants"], err = json.Marshal(tenants); err != nil {
		t.Fatal(err)
	}
	if raw, err = json.Marshal(doc); err != nil {
		t.Fatal(err)
	}
	if h.svc.Catalog, err = config.LoadCatalog(raw); err != nil {
		t.Fatal(err)
	}
	h.svc.Trunks = transport.Trunks{"carrier-out": carrierTrunk}
	logs := &lockedBuffer{}
	h.svc.Log = slog.New(slog.NewJSONHandler(logs, &slog.HandlerOptions{Level: slog.LevelDebug}))
	return h, logs
}

const (
	tenantElsewhere    = "t_0e1f2a3b"
	tenantWithoutPhone = "t_5a6b7c8d"
)

func phoneSession(tenant, channel string) string {
	return `{"tenantId":"` + tenant + `","language":"hi","channel":"` + channel + `"}`
}

func guestSession(overrides string) string {
	return `{"tenantId":"` + tenantID + `","language":"hi","channel":"webrtc","overrides":` + withGuests(overrides) + `}`
}

func withGuests(overrides string) string {
	guests := `"telephony":{"phoneGuests":"dial_out"}`
	if overrides == "" || overrides == "{}" {
		return "{" + guests + "}"
	}
	return "{" + guests + "," + strings.TrimPrefix(overrides, "{")
}

func (h *harness) dial(t *testing.T, sessionID, body string) (int, []byte) {
	t.Helper()
	return h.call(t, http.MethodPost, "/sessions/"+sessionID+"/call/start", body)
}

func TestAPhoneCallIsPlacedIntoTheStoredSessionUnderAMintedIdentity(t *testing.T) {
	t.Parallel()
	h, logs := servePhone(t)
	created := h.create(t, phoneSession(tenantID, "telephony"))
	if len(h.transport.dispatched) != 1 {
		t.Fatalf("the agent was not dispatched before the call: %+v", h.transport.dispatched)
	}

	status, raw := h.dial(t, created.SessionID, `{"to":"`+callee+`"}`)
	if status != http.StatusCreated {
		t.Fatalf("call/start returned %d: %s", status, raw)
	}
	var placed struct {
		SessionID     string `json:"sessionId"`
		ParticipantID string `json:"participantId"`
		CallID        string `json:"callId"`
	}
	if err := json.Unmarshal(raw, &placed); err != nil {
		t.Fatal(err)
	}
	if len(h.transport.calls) != 1 {
		t.Fatalf("calls %+v", h.transport.calls)
	}
	c := h.transport.calls[0]
	if c.Room != created.Room || c.To != callee || c.Trunk.Address != carrierTrunk.Address {
		t.Errorf("placed %+v; the call goes into the session's own room on its trunk", c)
	}
	if err := ids.ValidateID(ids.PrefixParticipant, c.Identity); err != nil || c.Identity == created.ParticipantID {
		t.Errorf("the phone joins as %q; it needs a fresh opaque participant id: %v", c.Identity, err)
	}
	if placed.ParticipantID != c.Identity || placed.SessionID != created.SessionID || placed.CallID != "SCL_stub" {
		t.Errorf("response %+v for identity %s", placed, c.Identity)
	}
	if c.RingingTimeout != 20*time.Second || c.MaxCallDuration != 30*time.Minute {
		t.Errorf("rings %s and lasts %s; the stored config states 20s and defaults the rest", c.RingingTimeout, c.MaxCallDuration)
	}

	stored, err := h.store.Session(t.Context(), created.SessionID)
	if err != nil {
		t.Fatal(err)
	}
	for where, text := range map[string]string{"response": string(raw), "stored config": string(stored.Config), "logs": logs.String()} {
		if strings.Contains(text, calleeTail) {
			t.Errorf("the dialed number reached the %s", where)
		}
	}
	if !strings.Contains(logs.String(), c.Identity) {
		t.Errorf("the placed call was not logged under its participant id: %s", logs)
	}
}

func TestACallIsRefusedUnlessTheStoredSessionCanPlaceIt(t *testing.T) {
	t.Parallel()
	h, logs := servePhone(t)
	phone := h.create(t, phoneSession(tenantID, "telephony"))
	meeting := h.create(t, phoneSession(tenantID, "webrtc"))
	trunkless := h.create(t, phoneSession(tenantWithoutPhone, "telephony"))

	for _, tc := range []struct {
		name, session, body, pointer string
	}{
		{"a meeting without phone guests", meeting.SessionID, `{"to":"` + callee + `"}`, "/telephony/phoneGuests"},
		{"a tenant with no trunk", trunkless.SessionID, `{"to":"` + callee + `"}`, "/telephony/trunk"},
		{"a local number", phone.SessionID, `{"to":"0` + calleeTail + `"}`, "/to"},
		{"no number", phone.SessionID, `{}`, "/to"},
	} {
		status, raw := h.dial(t, tc.session, tc.body)
		if status != http.StatusBadRequest || !strings.Contains(string(raw), tc.pointer) {
			t.Errorf("%s: %d %s, want 400 at %s", tc.name, status, raw, tc.pointer)
		}
		if strings.Contains(string(raw), calleeTail) {
			t.Errorf("%s: the refusal repeats the number: %s", tc.name, raw)
		}
	}
	for _, body := range []string{`{"to":"` + callee + `","from":"+12025550199"}`, `not json`} {
		if status, raw := h.dial(t, phone.SessionID, body); status != http.StatusBadRequest {
			t.Errorf("%s: %d %s", body, status, raw)
		}
	}
	if status, _ := h.dial(t, "s_00000000", `{"to":"`+callee+`"}`); status != http.StatusBadRequest {
		t.Errorf("an unknown session: %d", status)
	}
	if len(h.transport.calls) != 0 {
		t.Errorf("refused calls reached the media server: %+v", h.transport.calls)
	}
	if strings.Contains(logs.String(), calleeTail) {
		t.Errorf("a refusal logged the number: %s", logs)
	}
}

func TestASessionNamingAnUnknownTrunkIsNeverStored(t *testing.T) {
	t.Parallel()
	h, _ := servePhone(t)
	de := h.reject(t, phoneSession(tenantElsewhere, "telephony"), http.StatusBadRequest)
	if de.Code != errs.CodeInvalidConfig || !strings.Contains(strings.Join(de.Details, "\n"), "/telephony/trunk") {
		t.Errorf("want %s at /telephony/trunk, got %+v", errs.CodeInvalidConfig, de)
	}
	if h.transport.grant.Room != "" || len(h.transport.dispatched) != 0 {
		t.Error("a session with a trunk nobody configured was dispatched or minted a token")
	}
}

func TestAMediaServerRefusalOfTheCallReachesTheCallerWithoutTheNumber(t *testing.T) {
	t.Parallel()
	h, _ := servePhone(t)
	phone := h.create(t, phoneSession(tenantID, "telephony"))
	h.transport.callErr = errs.Errorf(errs.CodeProviderUnavailable, "media server unreachable for CreateSIPParticipant")
	status, raw := h.dial(t, phone.SessionID, `{"to":"`+callee+`"}`)
	if status != http.StatusServiceUnavailable || strings.Contains(string(raw), calleeTail) {
		t.Errorf("%d %s", status, raw)
	}
}
