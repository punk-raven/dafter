package control

import (
	"context"
	"errors"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/state"
	"github.com/punk-raven/dafter/go/internal/transport"
)

func (s *Service) resumeRecording(ctx context.Context, sess state.Session, cfg *config.ResolvedSessionConfig) {
	if rec := cfg.Recording; !rec.Enabled || rec.StartAt != config.StartAtSessionCreate {
		return
	}
	defer s.sessions.lock(sess.SessionID)()
	egresses, err := s.Store.Egresses(ctx, sess.SessionID)
	if err != nil {
		s.log().Warn("recording not resumed: egresses unreadable", "session", sess.SessionID, "error", err)
		return
	}
	layout := string(cfg.Recording.EffectiveLayout())
	for _, e := range egresses {
		if !e.Active() || e.Layout != layout {
			continue
		}
		file, err := s.Transport.RecordingFile(ctx, e.EgressID, sourceURLTTL)
		switch {
		case errors.Is(err, transport.ErrUnknownRecording):
		case err != nil:
			s.log().Warn("recording not resumed: egress state unknown", "session", sess.SessionID, "egress", e.EgressID, "error", err)
			return
		case !file.Ended:
			return
		}
		if _, err := s.settle(ctx, e, file.EndedAt); err != nil {
			s.log().Warn("ended egress not settled", "session", sess.SessionID, "egress", e.EgressID, "error", err)
		}
	}
	stored, _, err := s.startEgress(ctx, sess, cfg, cfg.Recording.EffectiveLayout(), startRecordingRequest{}, true, state.Egress{})
	if err != nil {
		s.log().Warn("recording not resumed for a returning joiner", "session", sess.SessionID, "error", err)
		return
	}
	s.log().Info("recording resumed for a reopened room", "session", sess.SessionID, "egress", stored.EgressID)
}
