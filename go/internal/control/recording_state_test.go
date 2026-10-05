package control_test

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/transport"
)

var endedAt = time.Date(2026, 9, 22, 10, 7, 30, 0, time.UTC)

func (h *harness) sources(t *testing.T, sessionID string) sourcesView {
	t.Helper()
	status, raw := h.as(t, workerSecret, http.MethodGet, "/sessions/"+sessionID+"/transcription/sources", "")
	if status != http.StatusOK {
		t.Fatalf("sources returned %d: %s", status, raw)
	}
	var out sourcesView
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	return out
}

func (h *harness) recordTracks(t *testing.T, sessionID string, tracks map[string]transport.TrackPublisher, order ...string) {
	t.Helper()
	for _, track := range order {
		h.transport.publish(track, tracks[track])
		h.recording(t, "start", sessionID, `{"trackId":"`+track+`"}`, http.StatusCreated)
	}
}

func (h *harness) endOnItsOwn(sessionID, egressID, status string, file bool) {
	f := transport.RecordingFile{EgressID: egressID, Status: status, Ended: true, EndedAt: endedAt}
	if file {
		f.Complete, f.Key, f.URL = true, sessionID+"/track-"+egressID+".ogg", "http://storage/"+egressID+"?signed"
	}
	h.transport.finish(f)
}

func TestTracksThatEndedWhenTheirSpeakersLeftBecomeSources(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, afterCallRequest("after_call"))
	h.recordTracks(t, created.SessionID, map[string]transport.TrackPublisher{
		"TR_asha":  {Identity: asha, Audio: true},
		"TR_agent": {Identity: "agent-AJ_x", Agent: true, Audio: true},
	}, "TR_asha", "TR_agent")
	h.endOnItsOwn(created.SessionID, "EG_stub1", "EGRESS_COMPLETE", true)
	h.endOnItsOwn(created.SessionID, "EG_stub2", "EGRESS_COMPLETE", true)

	got := h.sources(t, created.SessionID)
	if len(got.Pending) != 0 || len(got.Sources) != 2 {
		t.Fatalf("recordings that ended on their own: sources %+v pending %+v", got.Sources, got.Pending)
	}
	for _, rec := range h.read(t, created.SessionID).Recordings {
		if rec.StoppedAt == nil || !rec.StoppedAt.Equal(endedAt) {
			t.Errorf("recording %s reads back as %+v, want stopped when the media server ended it", rec.EgressID, rec)
		}
	}
	call := recordedCall{session: created, sources: got}
	if status, raw := h.submit(t, created.SessionID, versionEvent(t, call, "2026-09-24T10:31:12.004Z", nil)); status != http.StatusCreated {
		t.Errorf("a transcript of tracks that ended on their own: %d %s", status, raw)
	}
}

func TestARecordingThatEndedWithoutAFileIsSkippedNotPending(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, afterCallRequest("after_call"))
	tracks := map[string]transport.TrackPublisher{}
	order := []string{"TR_ok", "TR_failed", "TR_aborted", "TR_capped", "TR_cappedbare", "TR_forgotten", "TR_live", "TR_stopped"}
	for _, track := range order {
		tracks[track] = transport.TrackPublisher{Identity: asha, Audio: true}
	}
	h.recordTracks(t, created.SessionID, tracks, order...)
	h.endOnItsOwn(created.SessionID, "EG_stub1", "EGRESS_COMPLETE", true)
	h.endOnItsOwn(created.SessionID, "EG_stub2", "EGRESS_FAILED", false)
	h.endOnItsOwn(created.SessionID, "EG_stub3", "EGRESS_ABORTED", false)
	h.endOnItsOwn(created.SessionID, "EG_stub4", "EGRESS_LIMIT_REACHED", true)
	h.endOnItsOwn(created.SessionID, "EG_stub5", "EGRESS_LIMIT_REACHED", false)
	h.transport.forget("EG_stub6")
	h.recording(t, "stop", created.SessionID, `{"egressId":"EG_stub8"}`, http.StatusOK)
	h.transport.finish(transport.RecordingFile{EgressID: "EG_stub8", Status: "EGRESS_FAILED", Ended: true, EndedAt: endedAt})

	got := h.sources(t, created.SessionID)
	sources := map[string]bool{}
	for _, s := range got.Sources {
		sources[s.RecordingID] = true
	}
	if len(sources) != 2 || !sources["EG_stub1"] || !sources["EG_stub4"] {
		t.Errorf("sources %+v, want the complete track and the one cut at its limit", got.Sources)
	}
	skipped := map[string]string{}
	for _, s := range got.Skipped {
		skipped[s.RecordingID] = s.Reason
	}
	for id, status := range map[string]string{"EG_stub2": "EGRESS_FAILED", "EG_stub3": "EGRESS_ABORTED", "EG_stub5": "EGRESS_LIMIT_REACHED", "EG_stub6": "no longer knows", "EG_stub8": "EGRESS_FAILED"} {
		if !strings.Contains(skipped[id], status) {
			t.Errorf("%s skipped as %q, want a reason naming %s", id, skipped[id], status)
		}
	}
	if len(got.Pending) != 1 || got.Pending[0].RecordingID != "EG_stub7" || got.Pending[0].Reason != "still recording" {
		t.Errorf("pending %+v, want only the track still recording", got.Pending)
	}
}

func TestStoppingRecordingsThatAlreadyEndedSucceeds(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, afterCallRequest("after_call"))
	h.recordTracks(t, created.SessionID, map[string]transport.TrackPublisher{
		"TR_asha": {Identity: asha, Audio: true},
		"TR_ravi": {Identity: ravi, Audio: true},
	}, "TR_asha", "TR_ravi")
	h.endOnItsOwn(created.SessionID, "EG_stub1", "EGRESS_COMPLETE", true)

	all := h.recording(t, "stop", created.SessionID, "", http.StatusOK)
	byID := map[string]recordingView{}
	for _, rec := range all.Recordings {
		byID[rec.EgressID] = rec
	}
	left, stopped := byID["EG_stub1"], byID["EG_stub2"]
	if left.StoppedAt == nil || !left.StoppedAt.Equal(endedAt) || left.Status != "EGRESS_COMPLETE" {
		t.Errorf("the track whose speaker left: %+v", left)
	}
	if stopped.StoppedAt == nil || stopped.Status != "EGRESS_ENDING" {
		t.Errorf("the track still running: %+v", stopped)
	}
	again := h.recording(t, "stop", created.SessionID, `{"egressId":"EG_stub1"}`, http.StatusOK)
	if len(again.Recordings) != 1 || again.Recordings[0].StoppedAt == nil || !again.Recordings[0].StoppedAt.Equal(endedAt) {
		t.Errorf("stopping a recording that already ended: %+v", again)
	}
}

func TestAStopTheMediaServerRefusesForARunningRecordingStillFails(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, afterCallRequest("after_call"))
	h.recordTracks(t, created.SessionID, map[string]transport.TrackPublisher{"TR_asha": {Identity: asha, Audio: true}}, "TR_asha")
	h.transport.mu.Lock()
	h.transport.egressErr = errs.Errorf(errs.CodeProviderUnavailable, "no egress available")
	h.transport.mu.Unlock()
	if status, raw := h.call(t, http.MethodPost, "/sessions/"+created.SessionID+"/recording/stop", ""); status != http.StatusServiceUnavailable {
		t.Errorf("a refused stop answered %d %s", status, raw)
	}
	if rec := h.read(t, created.SessionID).Recordings[0]; rec.StoppedAt != nil {
		t.Errorf("a recording the media server still runs was marked stopped: %+v", rec)
	}
}
