package control_test

import (
	"net/http"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/transport"
)

func (h *harness) mediaServerHook(t *testing.T, hook transport.Webhook, signature string) int {
	t.Helper()
	h.transport.mu.Lock()
	h.transport.hook = hook
	h.transport.mu.Unlock()
	req, err := http.NewRequestWithContext(t.Context(), http.MethodPost, h.server.URL+"/livekit/webhook", strings.NewReader("{}"))
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", signature)
	resp, err := h.server.Client().Do(req)
	if err != nil {
		t.Fatalf("post webhook: %v", err)
	}
	closeBody(t, resp)
	return resp.StatusCode
}

func voicesRequest(tracks bool) string {
	body := recordingRequest("room_composite", "session_create")
	if tracks {
		body = strings.Replace(body, `"enabled":true`, `"enabled":true,"tracks":true`, 1)
	}
	return body
}

func TestEachVoiceIsRecordedOnItsOwnWhenTheSessionAsks(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, voicesRequest(true))
	h.transport.publish("TR_AMasha01", transport.TrackPublisher{Identity: "p_4b81e0d7", Audio: true})
	h.transport.publish("TR_VCasha01", transport.TrackPublisher{Identity: "p_4b81e0d7"})
	h.transport.publish("TR_AMagent1", transport.TrackPublisher{Identity: "agent-AJ_1", Agent: true, Audio: true})

	for _, track := range []string{"TR_AMasha01", "TR_VCasha01", "TR_AMagent1", "TR_AMasha01"} {
		if status := h.mediaServerHook(t, transport.Webhook{Event: transport.EventTrackPublished, Room: created.Room, TrackID: track}, "signed"); status != http.StatusOK {
			t.Fatalf("webhook for %s answered %d", track, status)
		}
	}
	started, _ := h.transport.egresses()
	var voices []transport.EgressRequest
	for _, e := range started {
		if e.Layout == config.LayoutTrack {
			voices = append(voices, e)
		}
	}
	if len(voices) != 2 || voices[0].TrackID != "TR_AMasha01" || voices[1].TrackID != "TR_AMagent1" {
		t.Fatalf("voice recordings %+v; want each audio track once and no camera", voices)
	}
	view := h.read(t, created.SessionID)
	speakers := map[string]string{}
	for _, r := range view.Recordings {
		if r.Speaker != nil {
			speakers[r.TrackID] = r.Speaker.Kind + ":" + r.Speaker.ParticipantID
		}
	}
	if speakers["TR_AMasha01"] != "human:p_4b81e0d7" || speakers["TR_AMagent1"] != "agent:" {
		t.Errorf("voices are not kept with whose they are: %v", speakers)
	}
}

func TestAVoiceIsNotRecordedAloneUnlessAskedOrSigned(t *testing.T) {
	t.Parallel()
	h := serve(t)
	plain := h.create(t, voicesRequest(false))
	asked := h.create(t, voicesRequest(true))
	h.transport.publish("TR_AMasha01", transport.TrackPublisher{Identity: "p_4b81e0d7", Audio: true})

	if status := h.mediaServerHook(t, transport.Webhook{Event: transport.EventTrackPublished, Room: plain.Room, TrackID: "TR_AMasha01"}, "signed"); status != http.StatusOK {
		t.Fatalf("webhook answered %d", status)
	}
	if status := h.mediaServerHook(t, transport.Webhook{Event: transport.EventTrackPublished, Room: asked.Room, TrackID: "TR_AMasha01"}, "forged"); status != http.StatusUnauthorized {
		t.Errorf("an unsigned webhook answered %d, want 401", status)
	}
	h.mediaServerHook(t, transport.Webhook{Event: "track_unpublished", Room: asked.Room, TrackID: "TR_AMasha01"}, "signed")
	started, _ := h.transport.egresses()
	for _, e := range started {
		if e.Layout == config.LayoutTrack {
			t.Errorf("a voice was recorded alone: %+v", e)
		}
	}
}
