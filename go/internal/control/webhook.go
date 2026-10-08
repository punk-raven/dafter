package control

import (
	"context"
	"errors"
	"net/http"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/ids"
	"github.com/punk-raven/dafter/go/internal/state"
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
	at := hook.At
	if at.IsZero() {
		at = time.Now().UTC()
	}
	switch hook.Event {
	case transport.EventTrackPublished:
		s.recordVoice(ctx, hook.Room, hook.TrackID)
	case transport.EventParticipantJoined:
		if ids.ValidateID(ids.PrefixParticipant, hook.Participant) == nil {
			if err := s.Store.MarkJoined(ctx, hook.Room, at); err != nil && !errors.Is(err, state.ErrNotFound) {
				s.log().Warn("session join not recorded", "session", hook.Room, "error", err)
			}
		}
	case transport.EventEgressEnded:
		s.egressEnded(ctx, hook.Egress)
	case transport.EventRoomFinished:
		s.roomFinished(ctx, hook.Room, at)
	}
}

func (s *Service) egressEnded(ctx context.Context, info transport.EgressInfo) {
	if !egressIDPattern.MatchString(info.EgressID) {
		return
	}
	endedAt := info.EndedAt
	if endedAt.IsZero() {
		endedAt = time.Now().UTC()
	}
	if err := s.Store.StopEgress(ctx, info.EgressID, endedAt); err != nil && !errors.Is(err, state.ErrNotFound) {
		s.log().Warn("finished recording not settled", "egress", info.EgressID, "error", err)
	}
}

func (s *Service) roomFinished(ctx context.Context, sessionID string, at time.Time) {
	defer s.sessions.lock(sessionID)()
	ended, err := s.Store.EndSession(ctx, sessionID, at)
	if err != nil {
		s.log().Warn("session end not recorded", "session", sessionID, "error", err)
		return
	}
	egresses, err := s.Store.Egresses(ctx, sessionID)
	if err != nil {
		s.log().Warn("recordings of a closed room not settled", "session", sessionID, "error", err)
	}
	for _, e := range egresses {
		if !e.Active() {
			continue
		}
		if _, _, err := s.stopEgress(ctx, e); err != nil {
			s.log().Warn("recording of a closed room not stopped", "session", sessionID, "egress", e.EgressID, "error", err)
		}
	}
	if !ended {
		return
	}
	if err := s.Store.EndDialIn(ctx, sessionID); err != nil && !errors.Is(err, state.ErrNotFound) {
		s.log().Warn("dial-in of an ended call not closed", "session", sessionID, "error", err)
	}
	incSessionEnded()
	s.log().Info("call ended: everyone left and the room closed", "session", sessionID)
}

func (s *Service) recordVoice(ctx context.Context, room, trackID string) {
	if !trackIDPattern.MatchString(trackID) || !s.voices.claim(trackID) {
		return
	}
	defer s.voices.release(trackID)
	sess, err := s.Store.Session(ctx, room)
	if err != nil || sess.Ended() {
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
