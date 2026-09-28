package control

import (
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/events"
	"github.com/punk-raven/dafter/go/internal/state"
)

const maxMinutesBody = 1 << 20

type minutesView struct {
	SessionID string          `json:"sessionId"`
	Version   int             `json:"version"`
	StoredAt  time.Time       `json:"storedAt"`
	Minutes   json.RawMessage `json:"minutes,omitempty"`
}

func (s *Service) storeMinutes(w http.ResponseWriter, r *http.Request) {
	if !s.authenticWorker(w, r) {
		return
	}
	sess, ok := s.storedSession(w, r)
	if !ok {
		return
	}
	cfg, err := config.Parse(sess.Config)
	if err != nil {
		s.fail(w, errs.Wrap(errs.CodeInternal, err, "stored session document"))
		return
	}
	if !cfg.ScribeEnabled() {
		s.fail(w, located(errs.CodeInvalidConfig, "/scribe/enabled", "the scribe is off in this session's stored config, so it has no minutes"))
		return
	}
	raw, err := io.ReadAll(http.MaxBytesReader(w, r.Body, maxMinutesBody))
	if err != nil {
		s.fail(w, errs.Wrap(errs.CodeInvalidConfig, err, "read minutes"))
		return
	}
	payload, err := parseMinutesEvent(raw, sess)
	if err != nil {
		s.fail(w, err)
		return
	}
	stored, err := s.Store.AddMinutes(r.Context(), state.Minutes{SessionID: sess.SessionID, StoredAt: time.Now().UTC(), Document: payload})
	if err != nil {
		s.fail(w, err)
		return
	}
	s.log().Info("minutes stored", "session", sess.SessionID, "version", stored.Version)
	s.write(w, http.StatusCreated, minutesView{SessionID: sess.SessionID, Version: stored.Version, StoredAt: stored.StoredAt})
}

func parseMinutesEvent(raw []byte, sess state.Session) (json.RawMessage, error) {
	event, err := events.Parse(raw)
	if err != nil {
		var de *errs.Error
		if errors.As(err, &de) && de.Code == errs.CodeInternal {
			out := errs.Errorf(errs.CodeInvalidConfig, "the minutes are not a valid event")
			out.Details = de.Details
			return nil, out
		}
		return nil, err
	}
	if event.Type != events.EventScribeMinutes {
		return nil, located(errs.CodeInvalidConfig, "/type", "minutes arrive as scribe.minutes")
	}
	if event.SessionID != sess.SessionID || event.TenantID != sess.TenantID {
		return nil, located(errs.CodeInvalidConfig, "/sessionId", "the event names another session or tenant")
	}
	if final, _ := event.Payload["final"].(bool); !final {
		return nil, located(errs.CodeInvalidConfig, "/payload/final", "the control plane keeps the minutes written when the call ended, not minutes so far")
	}
	var envelope struct {
		Payload json.RawMessage `json:"payload"`
	}
	if err := json.Unmarshal(raw, &envelope); err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "decode minutes")
	}
	return envelope.Payload, nil
}

func (s *Service) readMinutes(w http.ResponseWriter, r *http.Request) {
	if !s.authenticWorker(w, r) {
		return
	}
	sess, ok := s.storedSession(w, r)
	if !ok {
		return
	}
	m, err := s.Store.LatestMinutes(r.Context(), sess.SessionID)
	if errors.Is(err, state.ErrNoMinutes) {
		s.fail(w, located(errs.CodeInvalidConfig, "/minutes", "this session has no minutes yet"))
		return
	}
	if err != nil {
		s.fail(w, err)
		return
	}
	s.write(w, http.StatusOK, minutesView{SessionID: sess.SessionID, Version: m.Version, StoredAt: m.StoredAt, Minutes: m.Document})
}
