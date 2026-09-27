package control_test

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/transport"
)

const (
	asha = "p_4b81e0d7"
	ravi = "p_9d02c3aa"
)

func afterCallRequest(mode string) string {
	return `{"tenantId":"` + tenantID + `","language":"hi","channel":"webrtc","overrides":{` +
		`"recording":{"enabled":true,"layout":"track","consentArtifactId":"consent_rec"},` +
		`"transcription":{"mode":"` + mode + `","consentArtifactId":"consent_tr"}}}`
}

func (h *harness) as(t *testing.T, credential, method, path, body string) (int, []byte) {
	t.Helper()
	req, err := http.NewRequestWithContext(t.Context(), method, h.server.URL+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	if credential != "" {
		req.Header.Set("Authorization", "Bearer "+credential)
	}
	resp, err := h.server.Client().Do(req)
	if err != nil {
		t.Fatalf("%s %s: %v", method, path, err)
	}
	defer closeBody(t, resp)
	raw, err := readAll(resp)
	if err != nil {
		t.Fatal(err)
	}
	return resp.StatusCode, raw
}

type sourceView struct {
	RecordingID string `json:"recordingId"`
	TrackID     string `json:"trackId"`
	Speaker     struct {
		Kind          string `json:"kind"`
		ParticipantID string `json:"participantId"`
	} `json:"speaker"`
	StartedAt time.Time `json:"startedAt"`
	URL       string    `json:"url"`
	ExpiresAt time.Time `json:"expiresAt"`
}

type heldView struct {
	RecordingID string `json:"recordingId"`
	Reason      string `json:"reason"`
}

type sourcesView struct {
	SessionID  string          `json:"sessionId"`
	ConfigHash string          `json:"configHash"`
	Config     json.RawMessage `json:"config"`
	Sources    []sourceView    `json:"sources"`
	Pending    []heldView      `json:"pending"`
	Skipped    []heldView      `json:"skipped"`
}

type recordedCall struct {
	session sessionResponse
	sources sourcesView
}

func recordCall(t *testing.T, h *harness) recordedCall {
	t.Helper()
	created := h.create(t, afterCallRequest("after_call"))
	tracks := map[string]transport.TrackPublisher{
		"TR_asha":  {Identity: asha, Audio: true},
		"TR_ravi":  {Identity: ravi, Audio: true},
		"TR_agent": {Identity: "agent-AJ_x", Agent: true, Audio: true},
		"TR_video": {Identity: asha},
		"TR_lk":    {Identity: "loadtest-7", Audio: true},
		"TR_late":  {Identity: ravi, Audio: true},
	}
	for _, track := range []string{"TR_asha", "TR_ravi", "TR_agent", "TR_video", "TR_lk", "TR_late"} {
		h.transport.publish(track, tracks[track])
		h.recording(t, "start", created.SessionID, `{"trackId":"`+track+`"}`, http.StatusCreated)
	}
	for _, id := range []string{"EG_stub1", "EG_stub2", "EG_stub3", "EG_stub4", "EG_stub5"} {
		h.recording(t, "stop", created.SessionID, `{"egressId":"`+id+`"}`, http.StatusOK)
	}
	for _, id := range []string{"EG_stub1", "EG_stub3"} {
		h.transport.finish(transport.RecordingFile{
			EgressID: id, Status: "EGRESS_COMPLETE", Complete: true,
			Key: created.SessionID + "/track-" + id + ".ogg", URL: "http://storage/" + id + "?signed",
		})
	}
	h.transport.finish(transport.RecordingFile{EgressID: "EG_stub2", Status: "EGRESS_ENDING"})

	status, raw := h.as(t, workerSecret, http.MethodGet, "/sessions/"+created.SessionID+"/transcription/sources", "")
	if status != http.StatusOK {
		t.Fatalf("sources returned %d: %s", status, raw)
	}
	var out sourcesView
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	return recordedCall{session: created, sources: out}
}

func TestTheBatchPassGetsEveryFinishedAttributedTrackAndNothingElse(t *testing.T) {
	t.Parallel()
	h := serve(t)
	call := recordCall(t, h)
	got := call.sources
	if got.SessionID != call.session.SessionID || got.ConfigHash != call.session.ConfigHash || len(got.Config) == 0 {
		t.Errorf("sources name %s %s", got.SessionID, got.ConfigHash)
	}
	if len(got.Sources) != 2 {
		t.Fatalf("sources %+v", got.Sources)
	}
	byID := map[string]sourceView{}
	for _, s := range got.Sources {
		byID[s.RecordingID] = s
	}
	human, agent := byID["EG_stub1"], byID["EG_stub3"]
	if human.Speaker.Kind != "human" || human.Speaker.ParticipantID != asha || human.TrackID != "TR_asha" ||
		human.URL != "http://storage/EG_stub1?signed" || human.StartedAt.IsZero() || human.ExpiresAt.IsZero() {
		t.Errorf("human source %+v", human)
	}
	if agent.Speaker.Kind != "agent" || agent.Speaker.ParticipantID != "" {
		t.Errorf("agent source %+v", agent)
	}
	pending := map[string]string{}
	for _, p := range got.Pending {
		pending[p.RecordingID] = p.Reason
	}
	if !strings.Contains(pending["EG_stub2"], "EGRESS_ENDING") || pending["EG_stub6"] != "still recording" || len(pending) != 2 {
		t.Errorf("pending %+v", got.Pending)
	}
	skipped := map[string]bool{}
	for _, s := range got.Skipped {
		skipped[s.RecordingID] = true
	}
	if !skipped["EG_stub4"] || !skipped["EG_stub5"] || len(skipped) != 2 {
		t.Errorf("skipped %+v", got.Skipped)
	}
}

func TestSourcesTakeTheWorkerCredentialAndATranscriptionAfterTheCall(t *testing.T) {
	t.Parallel()
	h := serve(t)
	after := h.create(t, afterCallRequest("after_call"))
	for _, credential := range []string{"", "not-the-secret"} {
		if status, _ := h.as(t, credential, http.MethodGet, "/sessions/"+after.SessionID+"/transcription/sources", ""); status != http.StatusUnauthorized {
			t.Errorf("credential %q: %d", credential, status)
		}
	}
	live := h.create(t, `{"tenantId":"`+tenantID+`","language":"hi","channel":"webrtc","overrides":{"transcription":{"mode":"live","consentArtifactId":"c"}}}`)
	status, raw := h.as(t, workerSecret, http.MethodGet, "/sessions/"+live.SessionID+"/transcription/sources", "")
	if status != http.StatusBadRequest || !strings.Contains(string(raw), "/transcription/mode") {
		t.Errorf("a live-only session handed out sources: %d %s", status, raw)
	}
}

func TestTheCatalogResolvesAnAfterCallSessionWithItsBatchProvider(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, afterCallRequest("both"))
	cfg, err := config.Parse(created.Config)
	if err != nil {
		t.Fatal(err)
	}
	if cfg.TranscriptionMode() != config.TranscriptionBoth || cfg.Transcription.Batch == nil ||
		cfg.Transcription.Batch.Provider != "sarvam" || cfg.Transcription.Batch.Model != "saaras:v3" {
		t.Errorf("transcription resolved as %+v", cfg.Transcription)
	}
}
