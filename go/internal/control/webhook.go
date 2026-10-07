package control

import (
	"context"
	"net/http"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/ids"
	"github.com/punk-raven/dafter/go/internal/transport"
)

const hookWorkTimeout = 2 * time.Minute

func (s *Service) mediaServerWebhook(w http.ResponseWriter, r *http.Request) {
	hook, err := s.Transport.ReadWebhook(r)
	if err != nil {
		s.fail(w, err)
		return
	}
	if ids.ValidateID(ids.PrefixSession, hook.Room) == nil {
		s.background(func(ctx context.Context) { s.handleHook(ctx, hook) })
	}
	w.WriteHeader(http.StatusOK)
}

func (s *Service) background(work func(context.Context)) {
	run := func() {
		ctx, cancel := context.WithTimeout(context.Background(), hookWorkTimeout)
		defer cancel()
		work(ctx)
	}
	if s.Background != nil {
		s.Background(run)
		return
	}
	go run()
}

func (s *Service) handleHook(ctx context.Context, hook transport.Webhook) {
	if hook.Event == transport.EventTrackPublished {
		s.recordVoice(ctx, hook.Room, hook.TrackID)
	}
}

func (s *Service) recordVoice(ctx context.Context, room, trackID string) {
	if !trackIDPattern.MatchString(trackID) || !s.voices.claim(trackID) {
		return
	}
	defer s.voices.release(trackID)
	sess, err := s.Store.Session(ctx, room)
	if err != nil {
		return
	}
	cfg, err := config.Parse(sess.Config)
	if err != nil || !cfg.Recording.Enabled || !cfg.Recording.Tracks || cfg.Recording.ConsentArtifactID == "" {
		return
	}
	if s.alreadyRecording(ctx, sess.SessionID, trackID) {
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
