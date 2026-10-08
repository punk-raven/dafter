package control_test

import (
	"errors"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/state"
	"github.com/punk-raven/dafter/go/internal/transport"
)

var roomClosedAt = time.Date(2026, 10, 6, 20, 0, 0, 0, time.UTC)

func (h *harness) personJoined(t *testing.T, room, identity string) {
	t.Helper()
	h.mediaServerHook(t, transport.Webhook{Event: transport.EventParticipantJoined, Room: room, Participant: identity, At: roomClosedAt.Add(-time.Minute)}, "signed")
}

func (h *harness) roomClosed(t *testing.T, room string) {
	t.Helper()
	h.mediaServerHook(t, transport.Webhook{Event: transport.EventRoomFinished, Room: room, At: roomClosedAt}, "signed")
}

func TestACallEndsWhenItsRoomClosesAfterSomeoneJoined(t *testing.T) {
	t.Parallel()
	h, _ := serveMeetings(t)
	meeting := h.openMeeting(t, "")
	recorded := h.create(t, recordingRequest("room_composite", "session_create"))

	for _, s := range []struct{ room, identity string }{{meeting.Room, "p_4b81e0d7"}, {recorded.Room, "p_9c2e11aa"}} {
		h.personJoined(t, s.room, s.identity)
		h.roomClosed(t, s.room)
	}

	view := h.read(t, recorded.SessionID)
	if view.EndedAt == nil || !view.EndedAt.Equal(roomClosedAt) {
		t.Errorf("the call is not marked ended when its room closed: %v", view.EndedAt)
	}
	for _, r := range view.Recordings {
		if r.StoppedAt == nil {
			t.Errorf("recording %s is still running after its room closed", r.EgressID)
		}
	}
	if _, stopped := h.transport.egresses(); len(stopped) != 1 {
		t.Errorf("the composite was not stopped when the room closed: %v", stopped)
	}
	if _, err := h.store.DialInPIN(t.Context(), meeting.SessionID); !errors.Is(err, state.ErrNotFound) {
		t.Errorf("an ended call kept its dial-in PIN: %v", err)
	}
	status, raw := h.join(t, recorded.SessionID, `{"recordingConsent":"consent_1"}`)
	if status != http.StatusGone || !strings.Contains(string(raw), `"session_ended"`) || !strings.Contains(string(raw), "has ended") {
		t.Errorf("a late joiner got %d %s, want 410 session_ended saying the call has ended", status, raw)
	}
	if started, _ := h.transport.egresses(); len(started) != 1 {
		t.Errorf("an ended call was reopened for a late joiner: %d recordings started", len(started))
	}
}

func TestARoomNobodyJoinedClosingDoesNotEndTheCall(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, request("en-IN", "webrtc"))
	h.personJoined(t, created.Room, "agent-AJ_7f3a9c21")
	h.personJoined(t, created.Room, "EG_abc123")
	h.roomClosed(t, created.Room)

	if view := h.read(t, created.SessionID); view.EndedAt != nil {
		t.Errorf("a call only the agent and a recorder joined ended at %v", view.EndedAt)
	}
	if status, raw := h.join(t, created.SessionID, `{}`); status != http.StatusOK {
		t.Errorf("a call nobody had joined refused its first joiner: %d %s", status, raw)
	}
}

func TestAFinishedRecordingIsSettledWhenTheMediaServerSaysSo(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, recordingRequest("room_composite", "session_create"))
	ended := roomClosedAt.Add(-30 * time.Second)
	h.mediaServerHook(t, transport.Webhook{
		Event: transport.EventEgressEnded, Room: created.Room,
		Egress: transport.EgressInfo{EgressID: "EG_stub1", Room: created.Room, Status: "EGRESS_COMPLETE", EndedAt: ended},
	}, "signed")
	view := h.read(t, created.SessionID)
	if len(view.Recordings) != 1 || view.Recordings[0].StoppedAt == nil || !view.Recordings[0].StoppedAt.Equal(ended) {
		t.Errorf("the recording was not settled at its end: %+v", view.Recordings)
	}
}

func TestAJoinNeverWaitsForAVoiceToStartRecording(t *testing.T) {
	t.Parallel()
	h := serve(t)
	h.svc.Background = func(work func()) { go work() }
	h.transport.trackGate = make(chan struct{})
	defer close(h.transport.trackGate)
	created := h.create(t, voicesRequest(true))
	h.transport.publish("TR_AMasha01", transport.TrackPublisher{Identity: "p_4b81e0d7", Audio: true})

	start := time.Now()
	if status := h.mediaServerHook(t, transport.Webhook{Event: transport.EventTrackPublished, Room: created.Room, TrackID: "TR_AMasha01"}, "signed"); status != http.StatusOK {
		t.Fatalf("webhook answered %d", status)
	}
	if status, raw := h.join(t, created.SessionID, `{"recordingConsent":"consent_1"}`); status != http.StatusOK {
		t.Fatalf("join answered %d %s", status, raw)
	}
	if waited := time.Since(start); waited > 2*time.Second {
		t.Errorf("the webhook and the join took %s while a voice recording was still starting", waited)
	}
}
