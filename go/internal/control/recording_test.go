package control_test

import (
	"bytes"
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/schema"
	"github.com/punk-raven/dafter/go/internal/transport"
)

type recordingView struct {
	EgressID  string     `json:"egressId"`
	Layout    string     `json:"layout"`
	Status    string     `json:"status,omitempty"`
	StartedAt time.Time  `json:"startedAt"`
	StoppedAt *time.Time `json:"stoppedAt,omitempty"`
}

type recordingResponse struct {
	SessionID  string          `json:"sessionId"`
	Recordings []recordingView `json:"recordings"`
}

type sessionView struct {
	SessionID    string          `json:"sessionId"`
	Room         string          `json:"room"`
	ConfigHash   string          `json:"configHash"`
	Config       json.RawMessage `json:"config"`
	Recordings   []recordingView `json:"recordings"`
	AgentRefusal json.RawMessage `json:"agentRefusal"`
}

func recordingRequest(layout, startAt string) string {
	rec := `{"enabled":true,"layout":"` + layout + `","consentArtifactId":"consent_1"`
	if startAt != "" {
		rec += `,"startAt":"` + startAt + `"`
	}
	rec += `}`
	return `{"tenantId":"` + tenantID + `","language":"en-IN","channel":"webrtc","overrides":{"recording":` + rec + `}}`
}

func (h *harness) call(t *testing.T, method, path, body string) (int, []byte) {
	t.Helper()
	req, err := http.NewRequestWithContext(t.Context(), method, h.server.URL+path, strings.NewReader(body))
	if err != nil {
		t.Fatal(err)
	}
	if body != "" {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := h.server.Client().Do(req)
	if err != nil {
		t.Fatalf("%s %s: %v", method, path, err)
	}
	defer closeBody(t, resp)
	raw, err := readAll(resp)
	if err != nil {
		t.Fatalf("read response: %v", err)
	}
	return resp.StatusCode, raw
}

func (h *harness) recording(t *testing.T, action, sessionID, body string, wantStatus int) recordingResponse {
	t.Helper()
	status, raw := h.call(t, http.MethodPost, "/sessions/"+sessionID+"/recording/"+action, body)
	if status != wantStatus {
		t.Fatalf("POST /sessions/{id}/recording/%s returned %d, want %d: %s", action, status, wantStatus, raw)
	}
	var out recordingResponse
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	return out
}

func (h *harness) rejectRecording(t *testing.T, action, sessionID, body string) *errs.Error {
	t.Helper()
	status, raw := h.call(t, http.MethodPost, "/sessions/"+sessionID+"/recording/"+action, body)
	if status != http.StatusBadRequest {
		t.Fatalf("POST /sessions/{id}/recording/%s returned %d, want %d: %s", action, status, http.StatusBadRequest, raw)
	}
	if err := schema.ValidateDocument(schema.Error, raw, errs.CodeInternal); err != nil {
		t.Errorf("the error body does not satisfy the error schema: %v", err)
	}
	var de errs.Error
	if err := json.Unmarshal(raw, &de); err != nil {
		t.Fatalf("decode error body: %v", err)
	}
	return &de
}

func (h *harness) read(t *testing.T, sessionID string) sessionView {
	t.Helper()
	status, raw := h.call(t, http.MethodGet, "/sessions/"+sessionID, "")
	if status != http.StatusOK {
		t.Fatalf("GET /sessions/{id} returned %d: %s", status, raw)
	}
	var out sessionView
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	return out
}

func TestStartRecordsTheRoomCompositeFromTheStoredConfig(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, recordingRequest("room_composite", ""))

	out := h.recording(t, "start", created.SessionID, "", http.StatusCreated)
	if len(out.Recordings) != 1 || out.Recordings[0].EgressID != "EG_stub1" || out.Recordings[0].Status != "EGRESS_STARTING" {
		t.Fatalf("start answered %+v", out)
	}
	if out.Recordings[0].Layout != "room_composite" || out.Recordings[0].StartedAt.IsZero() {
		t.Errorf("recording view = %+v", out.Recordings[0])
	}

	started, _ := h.transport.egresses()
	if len(started) != 1 {
		t.Fatalf("%d egresses started", len(started))
	}
	req := started[0]
	if req.Room != created.Room || req.SessionID != created.SessionID || req.Layout != config.LayoutRoomComposite {
		t.Errorf("egress request = %+v", req)
	}
	if req.AudioOnly {
		t.Error("a webrtc session was recorded audio-only")
	}
	if req.Encoding == nil || req.Encoding.Width != 1280 || req.Encoding.Height != 720 ||
		req.Encoding.Framerate != 30 || req.Encoding.VideoBitrate != 3000 || req.Encoding.AudioBitrate != 128 ||
		req.Encoding.VideoCodec != config.EgressCodecH264Main {
		t.Errorf("the encode did not come from the shipped catalog: %+v", req.Encoding)
	}

	view := h.read(t, created.SessionID)
	if len(view.Recordings) != 1 || view.Recordings[0].EgressID != "EG_stub1" || view.Recordings[0].StoppedAt != nil {
		t.Errorf("session read shows %+v; the egress id and start time belong on the session", view.Recordings)
	}
	if view.ConfigHash != created.ConfigHash || !bytes.Equal(view.Config, created.Config) {
		t.Error("the session read does not return the stored document")
	}
}

func TestStartTakesTrackIDsForTheTrackLayouts(t *testing.T) {
	t.Parallel()
	h := serve(t)

	composite := h.create(t, recordingRequest("track_composite", ""))
	h.recording(t, "start", composite.SessionID, `{"audioTrackId":"TR_a1","videoTrackId":"TR_v1"}`, http.StatusCreated)

	track := h.create(t, recordingRequest("track", ""))
	h.recording(t, "start", track.SessionID, `{"trackId":"TR_a2"}`, http.StatusCreated)

	started, _ := h.transport.egresses()
	if len(started) != 2 {
		t.Fatalf("%d egresses started", len(started))
	}
	if started[0].Layout != config.LayoutTrackComposite || started[0].AudioTrackID != "TR_a1" || started[0].VideoTrackID != "TR_v1" {
		t.Errorf("track composite request = %+v", started[0])
	}
	if started[1].Layout != config.LayoutTrack || started[1].TrackID != "TR_a2" {
		t.Errorf("track request = %+v", started[1])
	}
	if started[0].CreateRoom || started[1].CreateRoom {
		t.Error("an on-demand start asked for the room to be created; a closed room would be recorded empty")
	}

	cases := []struct {
		name, sessionID, body, pointer string
	}{
		{"track composite without tracks", composite.SessionID, `{}`, "/audioTrackId"},
		{"track without a track", track.SessionID, ``, "/trackId"},
		{"track id on a composite", composite.SessionID, `{"trackId":"TR_a2"}`, "/trackId"},
		{"a track id the server could not have minted", track.SessionID, `{"trackId":"jane@example.com"}`, "/trackId"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			de := h.rejectRecording(t, "start", tc.sessionID, tc.body)
			if !strings.Contains(strings.Join(de.Details, "\n"), tc.pointer) {
				t.Errorf("no detail points at %s: %v", tc.pointer, de.Details)
			}
		})
	}
	if again, _ := h.transport.egresses(); len(again) != 2 {
		t.Errorf("a rejected start reached the media server: %d egresses", len(again))
	}
}

func TestRecordingIsRefusedWhereTheStoredConfigForbidsIt(t *testing.T) {
	t.Parallel()
	h := serve(t)
	plain := h.create(t, request("en-IN", "webrtc"))

	de := h.rejectRecording(t, "start", plain.SessionID, "")
	if de.Code != errs.CodeInvalidConfig || !strings.Contains(strings.Join(de.Details, "\n"), "/recording/enabled") {
		t.Errorf("a session that resolved without recording was not refused at /recording/enabled: %v", de)
	}
	if de = h.rejectRecording(t, "stop", plain.SessionID, ""); de.Code != errs.CodeInvalidConfig {
		t.Errorf("stop on an unrecorded session: %v", de)
	}
	if de = h.rejectRecording(t, "start", "s_00000000", ""); de.Code != errs.CodeInvalidConfig {
		t.Errorf("start on an unknown session: %v", de)
	}
	if de = h.rejectRecording(t, "start", plain.SessionID, `{"admin":true}`); de.Code != errs.CodeInvalidConfig {
		t.Errorf("an unknown request field was accepted: %v", de)
	}
	if started, _ := h.transport.egresses(); len(started) != 0 {
		t.Errorf("%d egresses started for refused requests", len(started))
	}
}

func TestStopEndsTheRunningRecordingsAndTheSessionReadShowsIt(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, recordingRequest("track", ""))
	h.recording(t, "start", created.SessionID, `{"trackId":"TR_a1"}`, http.StatusCreated)
	h.recording(t, "start", created.SessionID, `{"trackId":"TR_v1"}`, http.StatusCreated)

	one := h.recording(t, "stop", created.SessionID, `{"egressId":"EG_stub1"}`, http.StatusOK)
	if len(one.Recordings) != 1 || one.Recordings[0].EgressID != "EG_stub1" || one.Recordings[0].StoppedAt == nil {
		t.Fatalf("stop by id answered %+v", one)
	}
	if one.Recordings[0].Status != "EGRESS_ENDING" {
		t.Errorf("status %q is not what the media server said", one.Recordings[0].Status)
	}

	rest := h.recording(t, "stop", created.SessionID, "", http.StatusOK)
	if len(rest.Recordings) != 1 || rest.Recordings[0].EgressID != "EG_stub2" {
		t.Fatalf("stop without an id should end what is still running: %+v", rest)
	}
	if _, stopped := h.transport.egresses(); len(stopped) != 2 || stopped[0] != "EG_stub1" || stopped[1] != "EG_stub2" {
		t.Errorf("media server was told to stop %v", stopped)
	}

	de := h.rejectRecording(t, "stop", created.SessionID, "")
	if !strings.Contains(strings.Join(de.Details, "\n"), "/egressId") {
		t.Errorf("stopping with nothing running: %v", de)
	}
	if de = h.rejectRecording(t, "stop", created.SessionID, `{"egressId":"not-an-egress"}`); !strings.Contains(strings.Join(de.Details, "\n"), "/egressId") {
		t.Errorf("a malformed egress id: %v", de)
	}

	view := h.read(t, created.SessionID)
	if len(view.Recordings) != 2 {
		t.Fatalf("session read shows %d recordings", len(view.Recordings))
	}
	for _, rec := range view.Recordings {
		if rec.StoppedAt == nil || !rec.StoppedAt.After(rec.StartedAt) {
			t.Errorf("recording %s reads back as %+v after a stop", rec.EgressID, rec)
		}
	}
}

func TestSessionCreateStartsTheRoomCompositeBeforeATokenExists(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, recordingRequest("room_composite", "session_create"))

	started, _ := h.transport.egresses()
	if len(started) != 1 || started[0].Room != created.Room || started[0].Layout != config.LayoutRoomComposite {
		t.Fatalf("session_create did not start a room composite: %+v", started)
	}
	if !started[0].CreateRoom {
		t.Error("the room was not created first; the media server refuses an egress on a room nobody has joined")
	}
	view := h.read(t, created.SessionID)
	if len(view.Recordings) != 1 || view.Recordings[0].EgressID != "EG_stub1" {
		t.Errorf("the automatic recording is not on the session: %+v", view.Recordings)
	}

	later := h.create(t, recordingRequest("room_composite", "first_publish"))
	if again, _ := h.transport.egresses(); len(again) != 1 {
		t.Errorf("first_publish started an egress at create: %+v", again)
	}
	if view := h.read(t, later.SessionID); len(view.Recordings) != 0 {
		t.Errorf("a first_publish session shows recordings at create: %+v", view.Recordings)
	}

	h.transport.egressErr = errs.Errorf(errs.CodeProviderUnavailable, "no egress available")
	h.transport.grant = transport.Grant{}
	status, raw := h.post(t, recordingRequest("room_composite", "session_create"))
	if status != http.StatusServiceUnavailable {
		t.Fatalf("a create whose evidence-grade recording could not start returned %d: %s", status, raw)
	}
	if h.transport.grant.Room != "" {
		t.Error("a token was minted for a session whose recording never started")
	}
}

func TestReadingAnUnknownSessionFails(t *testing.T) {
	t.Parallel()
	h := serve(t)
	for _, id := range []string{"s_00000000", "not-a-session"} {
		if status, _ := h.call(t, http.MethodGet, "/sessions/"+id, ""); status != http.StatusBadRequest {
			t.Errorf("GET /sessions/%s returned %d", id, status)
		}
	}
}
