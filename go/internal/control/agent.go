package control

import (
	"net/http"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/state"
)

type agentResponse struct {
	SessionID       string   `json:"sessionId"`
	AgentDispatchID string   `json:"agentDispatchId,omitempty"`
	Recalled        []string `json:"recalled"`
}

func (s *Service) inviteAgent(w http.ResponseWriter, r *http.Request) {
	sess, cfg, ok := s.agentSession(w, r)
	if !ok {
		return
	}
	if !cfg.Agent.Enabled {
		s.fail(w, located(errs.CodeInvalidConfig, "/agent/enabled", "the agent is off in this session's stored config"))
		return
	}

	recalled, ok := s.recallAgents(w, r, sess, cfg)
	if !ok {
		return
	}
	if err := s.Store.SetAgentRefusal(r.Context(), sess.SessionID, nil); err != nil {
		s.fail(w, err)
		return
	}
	dispatchID, err := s.dispatchAgent(r.Context(), sess, cfg)
	if err != nil {
		s.fail(w, err)
		return
	}
	s.write(w, http.StatusCreated, agentResponse{SessionID: sess.SessionID, AgentDispatchID: dispatchID, Recalled: recalled})
}

func (s *Service) removeAgent(w http.ResponseWriter, r *http.Request) {
	sess, cfg, ok := s.agentSession(w, r)
	if !ok {
		return
	}
	recalled, ok := s.recallAgents(w, r, sess, cfg)
	if !ok {
		return
	}
	s.write(w, http.StatusOK, agentResponse{SessionID: sess.SessionID, Recalled: recalled})
}

func (s *Service) agentSession(w http.ResponseWriter, r *http.Request) (state.Session, *config.ResolvedSessionConfig, bool) {
	sess, ok := s.storedSession(w, r)
	if !ok {
		return state.Session{}, nil, false
	}
	var empty struct{}
	if !s.decodeOptional(w, r, &empty, "decode agent request") {
		return state.Session{}, nil, false
	}
	cfg, err := config.Parse(sess.Config)
	if err != nil {
		s.fail(w, errs.Wrap(errs.CodeInternal, err, "stored session document"))
		return state.Session{}, nil, false
	}
	if cfg.PrivacyMode == config.PrivacySealed {
		s.fail(w, located(errs.CodePrivacyModeForbids, "/privacyMode", "a sealed session never has an agent"))
		return state.Session{}, nil, false
	}
	return sess, cfg, true
}

func (s *Service) recallAgents(w http.ResponseWriter, r *http.Request, sess state.Session, cfg *config.ResolvedSessionConfig) ([]string, bool) {
	if cfg.Agent.Pool == "" {
		return []string{}, true
	}
	recalled, err := s.Transport.RecallAgents(r.Context(), sess.Room, cfg.Agent.Pool)
	ids := make([]string, 0, len(recalled))
	for _, d := range recalled {
		ids = append(ids, d.DispatchID)
	}
	incRecall(len(ids))
	if err != nil {
		s.fail(w, err)
		return nil, false
	}
	if len(ids) > 0 {
		s.log().Info("agent recalled", "session", sess.SessionID, "dispatches", ids)
	}
	return ids, true
}
