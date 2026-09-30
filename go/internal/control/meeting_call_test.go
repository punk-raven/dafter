package control_test

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/transport"
)

func placedIdentity(t *testing.T, raw []byte) string {
	t.Helper()
	var placed struct {
		ParticipantID string `json:"participantId"`
	}
	if err := json.Unmarshal(raw, &placed); err != nil {
		t.Fatal(err)
	}
	return placed.ParticipantID
}

func (h *harness) hangUp(t *testing.T, sessionID, participantID string) (int, []byte) {
	t.Helper()
	return h.call(t, http.MethodPost, "/sessions/"+sessionID+"/call/"+participantID+"/stop", "")
}

func TestAMeetingThatTakesPhoneGuestsKeepsItsOwnTuning(t *testing.T) {
	t.Parallel()
	h, _ := servePhone(t)
	created := h.create(t, guestSession(""))
	cfg, err := config.Parse(created.Config)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.TrunkName() != "carrier-out" || !cfg.TakesPhoneCalls() {
		t.Errorf("trunk %q; a meeting asking for phone guests takes them on its tenant's trunk", cfg.TrunkName())
	}
	greets := cfg.Agent.Greets != nil && *cfg.Agent.Greets
	if cfg.Agent.Addressing.Mode != config.AddressingTranscript || greets || !cfg.VideoEnabled() {
		t.Errorf("addressing %s, greets %v, video %v: the telephony channel's tuning leaked into the meeting",
			cfg.Agent.Addressing.Mode, greets, cfg.VideoEnabled())
	}
	var doc struct {
		Agent struct {
			Pipeline map[string]struct {
				Options map[string]any `json:"options"`
			} `json:"pipeline"`
		} `json:"agent"`
	}
	if err := json.Unmarshal(created.Config, &doc); err != nil {
		t.Fatal(err)
	}
	if stt := doc.Agent.Pipeline["stt"].Options["sampleRate"]; stt != float64(16000) {
		t.Errorf("stt sample rate %v; the 8 kHz telephony overlay is the channel's, not the phone guests'", stt)
	}
}

func TestSeveralPhonesJoinOneMeetingEachUnderItsOwnIdentity(t *testing.T) {
	t.Parallel()
	h, logs := servePhone(t)
	created := h.create(t, guestSession(""))
	var identities []string
	for range 2 {
		status, raw := h.dial(t, created.SessionID, `{"to":"`+callee+`"}`)
		if status != http.StatusCreated {
			t.Fatalf("call/start into a meeting returned %d: %s", status, raw)
		}
		if strings.Contains(string(raw), calleeTail) {
			t.Errorf("the response repeats the number: %s", raw)
		}
		identities = append(identities, placedIdentity(t, raw))
	}
	if len(h.transport.calls) != 2 || identities[0] == identities[1] || identities[0] == created.ParticipantID {
		t.Fatalf("identities %v for calls %+v; each phone guest needs its own minted id", identities, h.transport.calls)
	}
	for i, c := range h.transport.calls {
		if c.Room != created.Room || c.Identity != identities[i] {
			t.Errorf("call %d went to %s as %s", i, c.Room, c.Identity)
		}
	}
	if strings.Contains(logs.String(), calleeTail) {
		t.Errorf("the number reached the logs: %s", logs)
	}
}

func TestAMeetingThatTakesPhonesRefusesEncryptionAndAnUntoldRecording(t *testing.T) {
	t.Parallel()
	h, _ := servePhone(t)
	for _, tc := range []struct {
		name, overrides, pointer string
		code                     errs.ErrorCode
	}{
		{"end to end", `{"privacyMode":"trusted_agent"}`, "/telephony/phoneGuests", errs.CodePrivacyModeForbids},
		{"sealed", `{"privacyMode":"sealed","agent":{"enabled":false}}`, "/telephony/phoneGuests", errs.CodePrivacyModeForbids},
		{"recorded without the agent", `{"agent":{"enabled":false},"recording":{"enabled":true,"layout":"track","consentArtifactId":"consent_rec"}}`, "/agent/enabled", errs.CodeInvalidConfig},
	} {
		body := guestSession(tc.overrides)
		de := h.reject(t, body, http.StatusBadRequest)
		if de.Code != tc.code || !strings.Contains(strings.Join(de.Details, "\n"), tc.pointer) {
			t.Errorf("%s: %s %v, want %s at %s", tc.name, de.Code, de.Details, tc.code, tc.pointer)
		}
	}
	if len(h.transport.dispatched) != 0 {
		t.Errorf("a refused meeting was dispatched: %+v", h.transport.dispatched)
	}
	h.create(t, guestSession(`{"recording":{"enabled":true,"layout":"track","consentArtifactId":"consent_rec"}}`))
}

func TestAHangUpEndsOnlyAPhoneOfThatSession(t *testing.T) {
	t.Parallel()
	h, logs := servePhone(t)
	meeting := h.create(t, guestSession(""))
	other := h.create(t, guestSession(""))
	status, raw := h.dial(t, meeting.SessionID, `{"to":"`+callee+`"}`)
	if status != http.StatusCreated {
		t.Fatalf("call/start returned %d: %s", status, raw)
	}
	phone := placedIdentity(t, raw)
	h.transport.people[meeting.ParticipantID] = meeting.Room

	for _, tc := range []struct {
		name, session, participant, pointer string
	}{
		{"another session's phone", other.SessionID, phone, "/participantId"},
		{"a person in the browser", meeting.SessionID, meeting.ParticipantID, "/participantId"},
		{"not a participant id", meeting.SessionID, "agent-AJ_x", "/participantId"},
	} {
		status, raw := h.hangUp(t, tc.session, tc.participant)
		if status != http.StatusBadRequest || !strings.Contains(string(raw), tc.pointer) {
			t.Errorf("%s: %d %s, want 400 at %s", tc.name, status, raw, tc.pointer)
		}
	}
	if len(h.transport.hungUp) != 0 {
		t.Fatalf("refused hang-ups reached the media server: %v", h.transport.hungUp)
	}

	status, raw = h.hangUp(t, meeting.SessionID, phone)
	if status != http.StatusOK {
		t.Fatalf("hang up returned %d: %s", status, raw)
	}
	if want := meeting.Room + "/" + phone; len(h.transport.hungUp) != 1 || h.transport.hungUp[0] != want {
		t.Errorf("hung up %v, want %s", h.transport.hungUp, want)
	}
	if placedIdentity(t, raw) != phone || !strings.Contains(logs.String(), "phone call hung up") {
		t.Errorf("response %s; logs %s", raw, logs)
	}
	if status, _ := h.hangUp(t, meeting.SessionID, phone); status != http.StatusBadRequest {
		t.Errorf("a phone already hung up answered %d", status)
	}
	if strings.Contains(logs.String(), calleeTail) {
		t.Errorf("the number reached the logs: %s", logs)
	}
}

var _ transport.Transport = (*stubTransport)(nil)
