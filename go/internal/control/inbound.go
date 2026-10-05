package control

import (
	"errors"
	"net/http"
	"slices"

	"github.com/punk-raven/dafter/go/internal/carrier/vobiz"
	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/ids"
	"github.com/punk-raven/dafter/go/internal/transport"
)

const carrierFormLimit = 16 << 10

func (s *Service) answerCall(w http.ResponseWriter, r *http.Request) {
	name, trunk, ok := s.inboundTrunk(w, r)
	if !ok || !s.signed(w, r, trunk) {
		return
	}
	answer, err := vobiz.ParseAnswer(r.PostForm)
	if err == nil && !slices.Contains(trunk.Numbers, answer.To) {
		err = located(errs.CodeInvalidConfig, "/To", "is not a number of this trunk")
	}
	if err != nil {
		s.refuseCall(w, name, err)
		return
	}
	if !s.held.fresh(r.Header.Get(vobiz.NonceHeader), answer.CallUUID) {
		s.forbidCall(w, name, "a signature nonce came back for another call")
		return
	}
	if call, ok := s.held.answered(answer.CallUUID); ok {
		s.writeXML(w, vobiz.Hold(call.token, s.heldURL(trunk, name, call.token), call.limit))
		return
	}
	if trunk.Inbound.AnswersMeetings(answer.To) {
		s.answerMeeting(w, r, name, trunk, answer)
		return
	}

	session := trunk.Inbound.Session
	opened, err := s.openSession(r.Context(), config.Request{
		TenantID: session.TenantID, Profile: session.Profile, Language: session.Language,
		Channel: config.ChannelTelephony,
	}, name)
	if err != nil {
		s.hangUp(w, name, err)
		return
	}
	call := heldCall{
		callUUID: answer.CallUUID, sessionID: opened.sess.SessionID, room: opened.sess.Room, trunk: name,
		ringing: opened.resolved.Config.RingingTimeout(), limit: opened.resolved.Config.MaxCallDuration(),
	}
	if call.identity, err = ids.NewID(ids.PrefixParticipant); err == nil {
		call.token, err = mintBridgeToken()
	}
	if err == nil {
		err = s.held.hold(call)
	}
	if err != nil {
		s.hangUp(w, name, err)
		return
	}
	incInbound("held")
	s.log().Info("inbound call held", "session", call.sessionID, "participant", call.identity, "trunk", name)
	s.writeXML(w, vobiz.Hold(call.token, s.heldURL(trunk, name, call.token), call.limit))
}

func (s *Service) callHeld(w http.ResponseWriter, r *http.Request) {
	name, trunk, ok := s.inboundTrunk(w, r)
	if !ok {
		return
	}
	token := r.PathValue("token")
	if !vobiz.TokenPattern.MatchString(token) || !s.held.known(token) {
		s.forbidCall(w, name, "no call is held under that token")
		return
	}
	callUUID, entered := vobiz.Entered(r.PostForm)
	if !entered {
		w.WriteHeader(http.StatusOK)
		return
	}
	call, first := s.held.dial(token, callUUID)
	if !first {
		w.WriteHeader(http.StatusOK)
		return
	}
	info, err := s.Transport.PlaceCall(r.Context(), call.dialBack(trunk))
	if err != nil {
		s.held.redial(token)
		incInbound("failed")
		var de *errs.Error
		if !errors.As(err, &de) {
			de = errs.Wrap(errs.CodeInternal, err, "dial the held caller")
		}
		s.log().Warn("held caller not dialed", "session", call.sessionID, "trunk", name, "code", de.Code)
		w.WriteHeader(http.StatusServiceUnavailable)
		return
	}
	incInbound("dialed")
	s.log().Info("held caller dialed", "session", call.sessionID, "participant", call.identity, "call", info.CallID)
	w.WriteHeader(http.StatusOK)
}

func (s *Service) bridgeCall(w http.ResponseWriter, r *http.Request) {
	name, trunk, ok := s.inboundTrunk(w, r)
	if !ok || !s.signed(w, r, trunk) {
		return
	}
	bridge, err := vobiz.ParseBridge(r.PostForm)
	if err != nil {
		s.hangUp(w, name, err)
		return
	}
	if !s.held.fresh(r.Header.Get(vobiz.NonceHeader), bridge.CallUUID) {
		s.forbidCall(w, name, "a signature nonce came back for another call")
		return
	}
	call, ok := s.held.bridge(bridge.Token)
	if !ok {
		s.hangUp(w, name, errs.Errorf(errs.CodeInvalidConfig, "no dialed call waits under that bridge token"))
		return
	}
	incInbound("bridged")
	s.log().Info("inbound call bridged", "session", call.sessionID, "participant", call.identity)
	s.writeXML(w, vobiz.Join(call.token, call.limit))
}

func (s *Service) inboundTrunk(w http.ResponseWriter, r *http.Request) (string, transport.Trunk, bool) {
	name := r.PathValue("trunk")
	trunk, ok := s.Trunks[name]
	if !ok || trunk.Inbound == nil {
		incInbound("refused")
		http.NotFound(w, r)
		return "", transport.Trunk{}, false
	}
	r.Body = http.MaxBytesReader(w, r.Body, carrierFormLimit)
	if err := r.ParseForm(); err != nil {
		s.refuseCall(w, name, errs.Wrap(errs.CodeInvalidConfig, err, "decode the carrier's webhook"))
		return "", transport.Trunk{}, false
	}
	return name, trunk, true
}

func (s *Service) signed(w http.ResponseWriter, r *http.Request, trunk transport.Trunk) bool {
	if _, ok := vobiz.Verify(trunk.Inbound.PublicURL+r.URL.Path, trunk.Inbound.SigningKey, r.Header); !ok {
		s.forbidCall(w, r.PathValue("trunk"), "the carrier's signature is missing or wrong")
		return false
	}
	return true
}

func (s *Service) heldURL(trunk transport.Trunk, name, token string) string {
	return trunk.Inbound.PublicURL + "/telephony/" + name + "/held/" + token
}

func (s *Service) forbidCall(w http.ResponseWriter, trunk, because string) {
	incInbound("refused")
	s.log().Warn("carrier webhook refused", "trunk", trunk, "because", because)
	w.WriteHeader(http.StatusForbidden)
}

func (s *Service) refuseCall(w http.ResponseWriter, trunk string, err error) {
	incInbound("refused")
	var de *errs.Error
	if !errors.As(err, &de) {
		de = errs.Wrap(errs.CodeInvalidConfig, err, "carrier webhook")
	}
	s.log().Warn("carrier webhook refused", "trunk", trunk, "code", de.Code, "details", de.Details)
	s.write(w, http.StatusBadRequest, de)
}

func (s *Service) hangUp(w http.ResponseWriter, trunk string, err error) {
	incInbound("hung_up")
	var de *errs.Error
	if !errors.As(err, &de) {
		de = errs.Wrap(errs.CodeInternal, err, "inbound call")
	}
	s.log().Warn("inbound call hung up", "trunk", trunk, "code", de.Code, "details", de.Details)
	s.writeXML(w, vobiz.Hangup())
}

func (s *Service) writeXML(w http.ResponseWriter, body []byte) {
	w.Header().Set("Content-Type", vobiz.ContentType)
	w.WriteHeader(http.StatusOK)
	if _, err := w.Write(body); err != nil {
		s.log().Error("write carrier answer", "error", err)
	}
}
