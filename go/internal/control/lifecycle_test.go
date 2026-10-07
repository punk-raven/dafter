package control_test

import (
	"net/http"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/transport"
)

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
