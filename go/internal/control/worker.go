package control

import (
	"bytes"
	"crypto/subtle"
	"encoding/json"
	"io"
	"net/http"
	"strings"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/schema"
)

type agentKeyRequest struct {
	ConfigHash string `json:"configHash"`
}

type agentKeyResponse struct {
	SessionID     string `json:"sessionId"`
	EncryptionKey string `json:"encryptionKey"`
}

func (s *Service) agentKey(w http.ResponseWriter, r *http.Request) {
	if !s.authenticWorker(w, r) {
		return
	}
	sess, ok := s.storedSession(w, r)
	if !ok {
		return
	}
	var req agentKeyRequest
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<10))
	d.DisallowUnknownFields()
	if err := d.Decode(&req); err != nil {
		s.fail(w, errs.Wrap(errs.CodeInvalidConfig, err, "decode agent key request"))
		return
	}
	if req.ConfigHash != sess.ConfigHash {
		s.fail(w, located(errs.CodeInvalidConfig, "/configHash", "is not the hash of this session's stored document"))
		return
	}
	cfg, err := config.Parse(sess.Config)
	if err != nil {
		s.fail(w, errs.Wrap(errs.CodeInternal, err, "stored session document"))
		return
	}
	if !cfg.Agent.Enabled {
		s.fail(w, located(errs.CodeInvalidConfig, "/agent/enabled", "the agent is off in this session's stored config"))
		return
	}
	if !cfg.PrivacyMode.DisclosesKeyTo(config.RoleAgent) {
		s.fail(w, located(errs.CodePrivacyModeForbids, "/privacyMode", "this privacy mode never discloses the session key to an agent"))
		return
	}
	key := keyFor(cfg, sess, config.RoleAgent)
	if key == "" {
		s.fail(w, located(errs.CodeUnsupportedCapability, "/media/encryption/keyModel", "the control plane holds no shared key for this session"))
		return
	}
	s.log().Info("session key disclosed to the agent", "session", sess.SessionID)
	s.write(w, http.StatusOK, agentKeyResponse{SessionID: sess.SessionID, EncryptionKey: key})
}

func (s *Service) agentRefusal(w http.ResponseWriter, r *http.Request) {
	if !s.authenticWorker(w, r) {
		return
	}
	sess, ok := s.storedSession(w, r)
	if !ok {
		return
	}
	refusal, err := parseRefusal(http.MaxBytesReader(w, r.Body, 1<<14))
	if err != nil {
		s.fail(w, err)
		return
	}
	if err := s.Store.SetAgentRefusal(r.Context(), sess.SessionID, refusal); err != nil {
		s.fail(w, err)
		return
	}
	s.log().Warn("agent refused the session", "session", sess.SessionID)
	w.WriteHeader(http.StatusNoContent)
}

func parseRefusal(body io.Reader) (json.RawMessage, error) {
	raw, err := io.ReadAll(body)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "read agent refusal")
	}
	if err := schema.ValidateDocument(schema.Error, raw, errs.CodeInvalidConfig); err != nil {
		return nil, err
	}
	var refusal errs.Error
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(&refusal); err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "decode agent refusal")
	}
	return json.Marshal(&refusal)
}

func (s *Service) authenticWorker(w http.ResponseWriter, r *http.Request) bool {
	presented, found := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
	if s.WorkerSecret == "" || !found ||
		subtle.ConstantTimeCompare([]byte(presented), []byte(s.WorkerSecret)) != 1 {
		s.fail(w, errs.Errorf(errs.CodeAuthenticationFailed, "this call takes the worker credential"))
		return false
	}
	return true
}
