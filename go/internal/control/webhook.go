package control

import (
	"context"
	"net/http"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/ids"
	"github.com/punk-raven/dafter/go/internal/transport"
)

func (s *Service) mediaServerWebhook(w http.ResponseWriter, r *http.Request) {
	hook, err := s.Transport.ReadWebhook(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if hook.Event == transport.EventTrackPublished {
		s.recordVoice(r.Context(), hook.Room, hook.TrackID)
	}
	w.WriteHeader(http.StatusOK)
}

func (s *Service) recordVoice(ctx context.Context, room, trackID string) {
	if ids.ValidateID(ids.PrefixSession, room) != nil || !trackIDPattern.MatchString(trackID) {
		return
	}
	sess, err := s.Store.Session(ctx, room)
	if err != nil {
		return
	}
	cfg, err := config.Parse(sess.Config)
	if err != nil || !cfg.Recording.Enabled || !cfg.Recording.Tracks || cfg.Recording.ConsentArtifactID == "" {
		return
	}
	owner, err := s.Transport.TrackOwner(ctx, sess.Room, trackID)
	if err != nil {
		s.log().Warn("voice not recorded: publisher unknown", "session", sess.SessionID, "track", trackID, "error", err)
		return
	}
	if !owner.Audio {
		return
	}
	s.recordingMu.Lock()
	defer s.recordingMu.Unlock()
	if s.alreadyRecording(ctx, sess.SessionID, trackID) {
		return
	}
	stored, _, err := s.startEgress(ctx, sess, cfg, config.LayoutTrack, startRecordingRequest{TrackID: trackID}, false, attributed(trackID, owner))
	if err != nil {
		s.log().Warn("voice not recorded", "session", sess.SessionID, "track", trackID, "error", err)
		return
	}
	s.log().Info("voice recorded on its own", "session", sess.SessionID, "track", trackID, "egress", stored.EgressID)
}

func (s *Service) alreadyRecording(ctx context.Context, sessionID, trackID string) bool {
	egresses, err := s.Store.Egresses(ctx, sessionID)
	if err != nil {
		return true
	}
	for _, e := range egresses {
		if e.TrackID == trackID && e.Active() {
			return true
		}
	}
	return false
}
