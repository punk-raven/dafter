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
