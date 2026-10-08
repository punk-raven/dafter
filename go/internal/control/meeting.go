package control

import (
	"context"
	"errors"
	"net/http"
	"slices"
	"time"

	"github.com/punk-raven/dafter/go/internal/carrier/vobiz"
	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/ids"
	"github.com/punk-raven/dafter/go/internal/state"
	"github.com/punk-raven/dafter/go/internal/transport"
)

type meeting struct {
	dialIn state.DialIn
	cfg    *config.ResolvedSessionConfig
}

func (s *Service) answerMeeting(w http.ResponseWriter, r *http.Request, name string, trunk transport.Trunk, answer vobiz.Answer) {
	if caller := vobiz.Caller(r.PostForm); caller != "" {
		matches, err := s.meetingsAllowing(r.Context(), caller)
		if err != nil {
			s.refuseMeeting(w, name, err)
			return
		}
		if len(matches) == 1 {
			s.admit(w, name, trunk, answer.CallUUID, matches[0])
			return
		}
	}
	if err := s.prompts.begin(answer.CallUUID); err != nil {
		s.refuseMeeting(w, name, err)
		return
	}
	incInbound("asked_pin")
	s.writeXML(w, vobiz.AskForPIN(s.pinURL(trunk, name), pinDigits, false))
}

func (s *Service) meetingPIN(w http.ResponseWriter, r *http.Request) {
	name, trunk, ok := s.inboundTrunk(w, r)
	if !ok || !s.signed(w, r, trunk) {
		return
	}
	digits, err := vobiz.ParseDigits(r.PostForm)
	if err == nil && !trunk.Inbound.AnswersMeetings(digits.To) {
		err = located(errs.CodeInvalidConfig, "/To", "is not a meeting number of this trunk")
	}
	if err != nil {
		s.refuseCall(w, name, err)
		return
	}
	if !s.held.fresh(r.Header.Get(vobiz.NonceHeader), digits.CallUUID) {
		s.forbidCall(w, name, "a signature nonce came back for another call")
		return
	}
	if call, ok := s.held.answered(digits.CallUUID); ok {
		s.writeXML(w, vobiz.Admit(call.token, s.heldURL(trunk, name, call.token), call.limit))
		return
	}
	found, err := s.meetingFor(r.Context(), digits.Entered, vobiz.Caller(r.PostForm))
	switch {
	case err != nil:
		s.refuseMeeting(w, name, err)
	case found != nil:
		s.prompts.done(digits.CallUUID)
		s.admit(w, name, trunk, digits.CallUUID, *found)
	case s.prompts.retry(digits.CallUUID, trunk.Inbound.Attempts()):
		incInbound("wrong_pin")
		s.writeXML(w, vobiz.AskForPIN(s.pinURL(trunk, name), pinDigits, true))
	default:
		s.refuseMeeting(w, name, errs.Errorf(errs.CodeAuthenticationFailed, "the caller used up their PIN attempts"))
	}
}

func (s *Service) meetingFor(ctx context.Context, pin, caller string) (*meeting, error) {
	if pin == "" {
		return nil, nil
	}
	d, err := s.Store.DialInByPIN(ctx, pin)
	if errors.Is(err, state.ErrNotFound) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	running, err := s.running(ctx, []state.DialIn{d})
	if err != nil || len(running) == 0 {
		return nil, err
	}
	m := running[0]
	if !m.cfg.CallerCheck().ChecksNumber() {
		return &m, nil
	}
	if caller == "" {
		return nil, nil
	}
	allowed, err := s.Store.DialInAllows(ctx, d.SessionID, caller)
	if err != nil || !allowed {
		return nil, err
	}
	return &m, nil
}

func (s *Service) meetingsAllowing(ctx context.Context, caller string) ([]meeting, error) {
	candidates, err := s.Store.DialInsAllowing(ctx, caller)
	if err != nil || len(candidates) == 0 {
		return nil, err
	}
	running, err := s.running(ctx, candidates)
	if err != nil {
		return nil, err
	}
	return slices.DeleteFunc(running, func(m meeting) bool { return m.cfg.CallerCheck() != config.CallerCheckNumber }), nil
}

func (s *Service) running(ctx context.Context, dialIns []state.DialIn) ([]meeting, error) {
	rooms := make([]string, 0, len(dialIns))
	for _, d := range dialIns {
		rooms = append(rooms, d.Room)
	}
	open, err := s.Transport.OpenRooms(ctx, rooms)
	if err != nil {
		return nil, err
	}
	var running []meeting
	now := time.Now().UTC()
	for _, d := range dialIns {
		if err := s.settleDialIn(ctx, d, open[d.Room], now); err != nil {
			return nil, err
		}
		if !open[d.Room] {
			continue
		}
		sess, err := s.Store.Session(ctx, d.SessionID)
		if err != nil {
			return nil, err
		}
		cfg, err := config.Parse(sess.Config)
		if err != nil {
			return nil, errs.Wrap(errs.CodeInternal, err, "stored session document")
		}
		if cfg.TakesDialIn() {
			running = append(running, meeting{dialIn: d, cfg: cfg})
		}
	}
	return running, nil
}

func (s *Service) admit(w http.ResponseWriter, name string, trunk transport.Trunk, callUUID string, m meeting) {
	call := heldCall{
		callUUID: callUUID, sessionID: m.dialIn.SessionID, room: m.dialIn.Room, trunk: name,
		ringing: m.cfg.RingingTimeout(), limit: m.cfg.MaxCallDuration(),
	}
	var err error
	if call.identity, err = ids.NewID(ids.PrefixParticipant); err == nil {
		call.token, err = mintBridgeToken()
	}
	if err == nil {
		err = s.held.hold(call)
	}
	if err != nil {
		s.refuseMeeting(w, name, err)
		return
	}
	incInbound("admitted")
	s.log().Info("dial-in call held", "session", call.sessionID, "participant", call.identity, "trunk", name)
	s.writeXML(w, vobiz.Admit(call.token, s.heldURL(trunk, name, call.token), call.limit))
}

func (s *Service) pinURL(trunk transport.Trunk, name string) string {
	return trunk.Inbound.PublicURL + "/telephony/" + name + "/pin"
}

func (s *Service) refuseMeeting(w http.ResponseWriter, trunk string, err error) {
	incInbound("hung_up")
	var de *errs.Error
	if !errors.As(err, &de) {
		de = errs.Wrap(errs.CodeInternal, err, "dial-in call")
	}
	s.log().Warn("dial-in call refused", "trunk", trunk, "code", de.Code)
	s.writeXML(w, vobiz.Refuse())
}
