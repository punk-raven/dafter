package control

import (
	"encoding/json"
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
	if cfg.Channel != config.ChannelTelephony {
		return transport.Trunk{}, located(errs.CodeInvalidConfig, "/channel", "only a telephony session places a phone call")
	}
	name := cfg.TrunkName()
	if name == "" {
		return transport.Trunk{}, located(errs.CodeInvalidConfig, "/telephony/trunk", "the session names no trunk to place a call on")
	}
	return s.knownTrunk(name)
}

func (s *Service) knownTrunk(name string) (transport.Trunk, error) {
	trunk, ok := s.Trunks[name]
	if !ok {
		return transport.Trunk{}, located(errs.CodeInvalidConfig, "/telephony/trunk", "names no trunk in the operator's trunk table")
	}
	return trunk, nil
}
