package control_test

import (
	"bytes"
	"encoding/json"
	"net/http"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/schema"
	"github.com/punk-raven/dafter/go/internal/transport"
)

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

func TestJoiningARecordedSessionNeedsTheConsentArtifactItWasShown(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, recordingRequest("room_composite", ""))
	h.transport.grant = transport.Grant{}

	for name, body := range map[string]string{
		"no consent":            `{"role":"participant"}`,
		"another artifact":      `{"role":"participant","recordingConsent":"consent_other"}`,
		"an observer, unstated": `{"role":"observer"}`,
	} {
		t.Run(name, func(t *testing.T) {
			status, raw := h.join(t, created.SessionID, body)
			if status != http.StatusBadRequest {
				t.Fatalf("returned %d, want %d: %s", status, http.StatusBadRequest, raw)
			}
			var de errs.Error
			if err := json.Unmarshal(raw, &de); err != nil {
				t.Fatalf("decode error body: %v", err)
			}
			if de.Code != errs.CodeConsentRequired || !strings.Contains(strings.Join(de.Details, " "), "/recordingConsent") {
				t.Errorf("want %s at /recordingConsent, got %s %v", errs.CodeConsentRequired, de.Code, de.Details)
			}
		})
	}
	if h.transport.grant.Room != "" {
		t.Fatal("a token was minted for a join that never consented to the recording")
	}

	if status, raw := h.join(t, created.SessionID, `{"role":"participant","recordingConsent":"consent_1"}`); status != http.StatusOK {
		t.Fatalf("a consenting join returned %d: %s", status, raw)
	}
	if h.transport.grant.Room != created.Room {
		t.Error("no token was minted for the consenting joiner")
	}
}

func TestAnUnrecordedSessionAsksNoRecordingConsent(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, request("en-IN", "webrtc"))
	if status, raw := h.join(t, created.SessionID, `{"recordingConsent":"consent_1"}`); status != http.StatusOK {
		t.Fatalf("returned %d: %s", status, raw)
	}
}

func TestAReturningJoinerRestartsTheRecordingOfAClosedRoom(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, recordingRequest("room_composite", "session_create"))
	consenting := `{"recordingConsent":"consent_1"}`

	if status, raw := h.join(t, created.SessionID, consenting); status != http.StatusOK {
		t.Fatalf("join returned %d: %s", status, raw)
	}
	if started, _ := h.transport.egresses(); len(started) != 1 {
		t.Fatalf("a join while the recording runs started another: %+v", started)
	}

	h.transport.finish(transport.RecordingFile{EgressID: "EG_stub1", Status: "EGRESS_COMPLETE", Ended: true})
	if status, raw := h.join(t, created.SessionID, consenting); status != http.StatusOK {
		t.Fatalf("join returned %d: %s", status, raw)
	}
	started, _ := h.transport.egresses()
	if len(started) != 2 || !started[1].CreateRoom || started[1].Layout != config.LayoutRoomComposite {
		t.Fatalf("the reopened room is not recorded: %+v", started)
	}
	view := h.read(t, created.SessionID)
	if len(view.Recordings) != 2 || view.Recordings[0].StoppedAt == nil || view.Recordings[1].StoppedAt != nil {
		t.Errorf("want the ended recording settled and a new one running: %+v", view.Recordings)
	}
}

func joinedAs(t *testing.T, h *harness, sessionID, body string) string {
	t.Helper()
	status, raw := h.join(t, sessionID, body)
	if status != http.StatusOK {
		t.Fatalf("join %s returned %d: %s", body, status, raw)
	}
	var joined sessionResponse
	if err := json.Unmarshal(raw, &joined); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	return joined.ParticipantID
}

func TestADeviceRejoinsUnderTheSameParticipant(t *testing.T) {
	t.Parallel()
	h := serve(t)
	first := h.create(t, request("en-IN", "webrtc"))
	second := h.create(t, request("en-IN", "webrtc"))
	const phone, laptop = `{"device":"dv_4b81e0d7a1c2f3e4b5a6c7d8"}`, `{"device":"dv_9c2e11aa0b1c2d3e4f5a6b7c"}`

	again := joinedAs(t, h, first.SessionID, phone)
	if got := joinedAs(t, h, first.SessionID, phone); got != again {
		t.Errorf("the same device rejoined as %q, then %q; the media server keeps both", again, got)
	}
	if got := joinedAs(t, h, first.SessionID, laptop); got == again {
		t.Errorf("two devices share participant %q", got)
	}
	if got := joinedAs(t, h, second.SessionID, phone); got == again {
		t.Errorf("one device is %q in two sessions, so its participant id links them", got)
	}
	if h.transport.grant.Identity != joinedAs(t, h, first.SessionID, phone) {
		t.Errorf("the token was minted for %q, not the device's participant", h.transport.grant.Identity)
	}
}

func TestADeviceKeyThatCouldCarryAnythingIsRefused(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, request("en-IN", "webrtc"))
	for _, device := range []string{"short", "jane@example.com-and-more-text", strings.Repeat("a", 65)} {
		status, raw := h.join(t, created.SessionID, `{"device":"`+device+`"}`)
		if status != http.StatusBadRequest || !bytes.Contains(raw, []byte("/device")) {
			t.Errorf("device %q: %d %s, want a 400 located at /device", device, status, raw)
		}
	}
}
