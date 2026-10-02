package control

import (
	"context"
	"net/http"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/state"
	"github.com/punk-raven/dafter/go/internal/transport"
)

func (s *Service) dispatchScribe(ctx context.Context, sess state.Session, cfg *config.ResolvedSessionConfig) string {
	if !cfg.ScribeEnabled() {
		return ""
	}
	pool := cfg.ScribePool()
	info, err := s.Transport.DispatchAgent(ctx, transport.AgentDispatch{
		Room:     sess.Room,
		Pool:     pool,
		Metadata: sess.Config,
	})
	if err != nil {
		incScribeDispatch(false)
		s.log().Warn("scribe not dispatched, the call goes on without notes", "session", sess.SessionID, "pool", pool, "error", err)
		return ""
	}
	incScribeDispatch(true)
	s.log().Info("scribe dispatched", "session", sess.SessionID, "pool", pool, "dispatch", info.DispatchID)
	return info.DispatchID
}

func (s *Service) scribeKey(w http.ResponseWriter, r *http.Request) {
	s.discloseKey(w, r, "scribe", (*config.ResolvedSessionConfig).ScribeEnabled, "/scribe/enabled")
}

func (s *Service) scribeRefusal(w http.ResponseWriter, r *http.Request) {
	s.recordRefusal(w, r, "scribe", s.Store.SetScribeRefusal)
}
