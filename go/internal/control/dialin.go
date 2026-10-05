package control

import (
	"context"
	"crypto/rand"
	"encoding/json"
	"errors"
	"fmt"
	"math/big"
	"net/http"
	"slices"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/state"
	"github.com/punk-raven/dafter/go/internal/transport"
)

const (
	pinDigits              = 8
	pinMints               = 8
	maxAllowedNumbers      = 50
	unopenedDialInLifetime = 24 * time.Hour
)

var pinSpace = big.NewInt(100_000_000)

type dialInDetails struct {
	Numbers []string `json:"numbers"`
	PIN     string   `json:"pin"`
}

func mintPIN() (string, error) {
	n, err := rand.Int(rand.Reader, pinSpace)
	if err != nil {
		return "", errs.Wrap(errs.CodeInternal, err, "mint meeting PIN")
	}
	return fmt.Sprintf("%0*d", pinDigits, n), nil
}

func (s *Service) meetingNumbers(cfg *config.ResolvedSessionConfig) ([]string, error) {
	trunk, err := s.knownTrunk(cfg.TrunkName())
	if err != nil {
		return nil, err
	}
	if trunk.Inbound == nil || len(trunk.Inbound.MeetingNumbers) == 0 {
		return nil, located(errs.CodeInvalidConfig, "/telephony/phoneGuests", "the tenant's trunk has no number that answers meeting dial-in calls")
	}
	return trunk.Inbound.MeetingNumbers, nil
}

func (s *Service) openDialIn(ctx context.Context, sess state.Session) error {
	for range pinMints {
		pin, err := mintPIN()
		if err != nil {
			return err
		}
		switch err := s.Store.CreateDialIn(ctx, sess.SessionID, pin, sess.CreatedAt); {
		case errors.Is(err, state.ErrPINTaken):
			continue
		case err != nil:
			return err
		}
		return nil
	}
	return errs.Errorf(errs.CodeRateLimited, "no free meeting PIN was found for the session")
}

func (s *Service) dialInFor(ctx context.Context, cfg *config.ResolvedSessionConfig, sessionID string, role config.Role) *dialInDetails {
	if !cfg.DisclosesDialInTo(role) {
		return nil
	}
	numbers, err := s.meetingNumbers(cfg)
	if err != nil {
		return nil
	}
	pin, err := s.Store.DialInPIN(ctx, sessionID)
	if err != nil {
		return nil
	}
	return &dialInDetails{Numbers: numbers, PIN: pin}
}

type allowedNumbersRequest struct {
	Numbers []string `json:"numbers"`
}

type allowedNumbersResponse struct {
	SessionID      string `json:"sessionId"`
	AllowedNumbers int    `json:"allowedNumbers"`
}

func (s *Service) setAllowedNumbers(w http.ResponseWriter, r *http.Request) {
	sess, ok := s.storedSession(w, r)
	if !ok {
		return
	}
	var req allowedNumbersRequest
	d := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4<<10))
	d.DisallowUnknownFields()
	if err := d.Decode(&req); err != nil {
		s.fail(w, errs.Wrap(errs.CodeInvalidConfig, errors.New("not a list of numbers"), "decode allowed numbers"))
		return
	}
	cfg, err := config.Parse(sess.Config)
	if err != nil {
		s.fail(w, errs.Wrap(errs.CodeInternal, err, "stored session document"))
		return
	}
	if err := allowedNumbersProblems(cfg, req.Numbers); err != nil {
		s.fail(w, err)
		return
	}
	numbers := slices.Compact(slices.Sorted(slices.Values(req.Numbers)))
	switch err := s.Store.SetDialInNumbers(r.Context(), sess.SessionID, numbers); {
	case errors.Is(err, state.ErrNotFound):
		s.fail(w, located(errs.CodeInvalidConfig, "/sessionId", "the session's dial-in has ended"))
		return
	case err != nil:
		s.fail(w, err)
		return
	}
	s.log().Info("dial-in numbers allowed", "session", sess.SessionID, "count", len(numbers))
	s.write(w, http.StatusOK, allowedNumbersResponse{SessionID: sess.SessionID, AllowedNumbers: len(numbers)})
}

func allowedNumbersProblems(cfg *config.ResolvedSessionConfig, numbers []string) error {
	switch {
	case !cfg.TakesDialIn():
		return located(errs.CodeInvalidConfig, "/telephony/phoneGuests", "the session takes no dial-in calls, so it allows no calling number")
	case !cfg.CallerCheck().ChecksNumber():
		return located(errs.CodeInvalidConfig, "/telephony/dialIn/callerCheck", "the session admits a caller by PIN alone, so it keeps no calling numbers")
	case len(numbers) > maxAllowedNumbers:
		return located(errs.CodeInvalidConfig, "/numbers", fmt.Sprintf("allows at most %d numbers", maxAllowedNumbers))
	}
	e := errs.Errorf(errs.CodeInvalidConfig, "allowed numbers")
	for i, n := range numbers {
		if !transport.PhoneNumber.MatchString(n) {
			e.Details = append(e.Details, fmt.Sprintf("at '/numbers/%d': is not an E.164 number, a plus and up to 15 digits", i))
		}
	}
	if len(e.Details) > 0 {
		e.Message = fmt.Sprintf("%d problem(s) with the allowed numbers", len(e.Details))
		return e
	}
	return nil
}

func (s *Service) SweepDialIns(ctx context.Context) error {
	dialIns, err := s.Store.DialIns(ctx)
	if err != nil || len(dialIns) == 0 {
		return err
	}
	rooms := make([]string, 0, len(dialIns))
	for _, d := range dialIns {
		rooms = append(rooms, d.Room)
	}
	open, err := s.Transport.OpenRooms(ctx, rooms)
	if err != nil {
		return err
	}
	now := time.Now().UTC()
	for _, d := range dialIns {
		if err := s.settleDialIn(ctx, d, open[d.Room], now); err != nil {
			return err
		}
	}
	return nil
}

func (s *Service) settleDialIn(ctx context.Context, d state.DialIn, open bool, now time.Time) error {
	switch {
	case open:
		return s.Store.MarkDialInOpened(ctx, d.SessionID, now)
	case d.Opened() || now.Sub(d.CreatedAt) > unopenedDialInLifetime:
		if err := s.Store.EndDialIn(ctx, d.SessionID); err != nil {
			return err
		}
		incInbound("dial_in_ended")
		s.log().Info("dial-in ended", "session", d.SessionID)
	}
	return nil
}

func (s *Service) SweepDialInsEvery(ctx context.Context, every time.Duration) {
	ticker := time.NewTicker(every)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			if err := s.SweepDialIns(ctx); err != nil {
				s.log().Warn("dial-in sweep failed", "error", err)
			}
		}
	}
}
