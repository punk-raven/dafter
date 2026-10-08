package control_test

import (
	"context"
	"net/http"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/transport"
)

func (s *stubTransport) publish(trackID string, p transport.TrackPublisher) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.owners == nil {
		s.owners = map[string]transport.TrackPublisher{}
	}
	s.owners[trackID] = p
}

func (s *stubTransport) ReadWebhook(r *http.Request) (transport.Webhook, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if r.Header.Get("Authorization") != "signed" {
		return transport.Webhook{}, errs.Errorf(errs.CodeAuthenticationFailed, "webhook is not signed by the media server")
	}
	return s.hook, nil
}

func (s *stubTransport) TrackOwner(_ context.Context, _, trackID string) (transport.TrackPublisher, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	p, ok := s.owners[trackID]
	if !ok {
		return transport.TrackPublisher{}, errs.Errorf(errs.CodeInvalidConfig, "no participant in the room publishes that track")
	}
	return p, nil
}

func (s *stubTransport) finish(f transport.RecordingFile) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.files == nil {
		s.files = map[string]transport.RecordingFile{}
	}
	s.files[f.EgressID] = f
}

func (s *stubTransport) forget(egressID string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.forgotten == nil {
		s.forgotten = map[string]bool{}
	}
	s.forgotten[egressID] = true
}

func (s *stubTransport) RecordingFile(_ context.Context, egressID string, ttl time.Duration) (transport.RecordingFile, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.forgotten[egressID] {
		return transport.RecordingFile{}, errs.Wrap(errs.CodeInvalidConfig, transport.ErrUnknownRecording, "the media server knows no recording under that id")
	}
	f, ok := s.files[egressID]
	if !ok {
		return transport.RecordingFile{EgressID: egressID, Status: "EGRESS_ACTIVE"}, nil
	}
	if f.Complete {
		f.ExpiresAt = time.Date(2026, 9, 24, 11, 0, 0, 0, time.UTC).Add(ttl)
	}
	return f, nil
}

func TestATrackRecordingSaysWhoseTrackItIs(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, recordingRequest("track", ""))
	h.transport.publish("TR_human", transport.TrackPublisher{Identity: "p_4b81e0d7", Audio: true})
	h.transport.publish("TR_agent", transport.TrackPublisher{Identity: "agent-AJ_x", Agent: true, Audio: true})
	h.transport.publish("TR_loadtest", transport.TrackPublisher{Identity: "loadtest-7", Audio: true})

	cases := []struct {
		track, kind, participant string
	}{
		{"TR_human", "human", "p_4b81e0d7"},
		{"TR_agent", "agent", ""},
		{"TR_loadtest", "", ""},
	}
	for _, tc := range cases {
		out := h.recording(t, "start", created.SessionID, `{"trackId":"`+tc.track+`"}`, http.StatusCreated)
		rec := out.Recordings[0]
		if rec.TrackID != tc.track {
			t.Errorf("%s: recording names track %q", tc.track, rec.TrackID)
		}
		switch {
		case tc.kind == "" && rec.Speaker != nil:
			t.Errorf("%s: a publisher the control plane never minted was attributed to %+v", tc.track, rec.Speaker)
		case tc.kind != "" && (rec.Speaker == nil || rec.Speaker.Kind != tc.kind || rec.Speaker.ParticipantID != tc.participant):
			t.Errorf("%s: speaker %+v, want %s %s", tc.track, rec.Speaker, tc.kind, tc.participant)
		}
	}
	read := h.read(t, created.SessionID)
	if len(read.Recordings) != 3 || read.Recordings[0].Speaker == nil || read.Recordings[0].Speaker.ParticipantID != "p_4b81e0d7" {
		t.Errorf("the session read lost the attribution: %+v", read.Recordings)
	}
}

func TestATrackNobodyPublishesIsNeverRecorded(t *testing.T) {
	t.Parallel()
	h := serve(t)
	created := h.create(t, recordingRequest("track", ""))
	de := h.rejectRecording(t, "start", created.SessionID, `{"trackId":"TR_gone"}`)
	if de.Code != errs.CodeInvalidConfig {
		t.Errorf("code %s", de.Code)
	}
	if started, _ := h.transport.egresses(); len(started) != 0 {
		t.Errorf("%d egresses started for a track nobody publishes", len(started))
	}
}
