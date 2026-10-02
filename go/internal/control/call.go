package control

import (
	"encoding/json"
	"errors"
	"net/http"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/ids"
	"github.com/punk-raven/dafter/go/internal/transport"
)

type startCallRequest struct {
	To string `json:"to"`
}

type callResponse struct {
	SessionID     string `json:"sessionId"`
	ParticipantID string `json:"participantId"`
	CallID        string `json:"callId,omitempty"`
}

func (s *Service) startCall(w http.ResponseWriter, r *http.Request) {
	sess, ok := s.storedSession(w, r)
	if !ok {
		return
	}
	var req startCallRequest
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<10))
	d.DisallowUnknownFields()
	if err := d.Decode(&req); err != nil {
		s.fail(w, errs.Wrap(errs.CodeInvalidConfig, err, "decode call request"))
		return
	}
	cfg, err := config.Parse(sess.Config)
	if err != nil {
		s.fail(w, errs.Wrap(errs.CodeInternal, err, "stored session document"))
		return
	}
	trunk, err := s.trunkFor(cfg)
	if err != nil {
		s.fail(w, err)
		return
	}
	if !transport.PhoneNumber.MatchString(req.To) {
		s.fail(w, located(errs.CodeInvalidConfig, "/to", "is not an E.164 number, a plus and up to 15 digits"))
		return
	}

	identity, err := ids.NewID(ids.PrefixParticipant)
	if err != nil {
		s.fail(w, errs.Wrap(errs.CodeInternal, err, "mint participant id"))
		return
	}
	info, err := s.Transport.PlaceCall(r.Context(), transport.PhoneCall{
		Room:            sess.Room,
		Identity:        identity,
		To:              req.To,
		Trunk:           trunk,
		RingingTimeout:  cfg.RingingTimeout(),
		MaxCallDuration: cfg.MaxCallDuration(),
	})
	incCall(err == nil)
	if err != nil {
		s.fail(w, err)
		return
	}
	s.log().Info("phone call placed", "session", sess.SessionID, "participant", identity, "trunk", cfg.TrunkName(), "call", info.CallID)
	s.write(w, http.StatusCreated, callResponse{SessionID: sess.SessionID, ParticipantID: identity, CallID: info.CallID})
}

func (s *Service) trunkFor(cfg *config.ResolvedSessionConfig) (transport.Trunk, error) {
	if !cfg.TakesPhoneCalls() {
		if cfg.Channel == config.ChannelTelephony {
			return transport.Trunk{}, located(errs.CodeInvalidConfig, "/telephony/trunk", "the tenant has no SIP trunk to place a call on")
		}
		return transport.Trunk{}, located(errs.CodeInvalidConfig, "/telephony/phoneGuests", "the session takes no phone guests, so no phone can be called into it")
	}
	return s.knownTrunk(cfg.TrunkName())
}

type hangUpResponse struct {
	SessionID     string `json:"sessionId"`
	ParticipantID string `json:"participantId"`
}

func (s *Service) stopCall(w http.ResponseWriter, r *http.Request) {
	sess, ok := s.storedSession(w, r)
	if !ok {
		return
	}
	identity := r.PathValue("participantID")
	if ids.ValidateID(ids.PrefixParticipant, identity) != nil {
		s.fail(w, located(errs.CodeInvalidConfig, "/participantId", "is not a participant id"))
		return
	}
	err := s.Transport.HangUp(r.Context(), sess.Room, identity)
	switch {
	case errors.Is(err, transport.ErrNoSuchParticipant):
		s.fail(w, located(errs.CodeInvalidConfig, "/participantId", "is not in this session's room"))
		return
	case errors.Is(err, transport.ErrNotAPhone):
		s.fail(w, located(errs.CodeInvalidConfig, "/participantId", "is not on a phone, and only a phone is hung up"))
		return
	case err != nil:
		s.fail(w, err)
		return
	}
	incHangUp()
	s.log().Info("phone call hung up", "session", sess.SessionID, "participant", identity)
	s.write(w, http.StatusOK, hangUpResponse{SessionID: sess.SessionID, ParticipantID: identity})
}

func (s *Service) knownTrunk(name string) (transport.Trunk, error) {
	trunk, ok := s.Trunks[name]
	if !ok {
		return transport.Trunk{}, located(errs.CodeInvalidConfig, "/telephony/trunk", "names no trunk in the operator's trunk table")
	}
	return trunk, nil
}
