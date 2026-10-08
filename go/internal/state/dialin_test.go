package state_test

import (
	"database/sql"
	"errors"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/state"
)

const (
	meetingPIN = "48151623"
	allowed    = "+919876543210"
	stranger   = "+919876500000"
)

func dialInSession(t *testing.T, s *state.Store, id string) {
	t.Helper()
	sess := session(t)
	sess.SessionID, sess.Room = id, id
	if err := s.CreateSession(t.Context(), sess); err != nil {
		t.Fatal(err)
	}
}

func TestADialInPINFindsItsSessionAndIsStoredOnlySealed(t *testing.T) {
	t.Parallel()
	path := filepath.Join(t.TempDir(), "dafter.db")
	s, err := state.Open(t.Context(), path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	dialInSession(t, s, "s_11111111")
	dialInSession(t, s, "s_22222222")
	at := time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)
	if err := s.CreateDialIn(t.Context(), "s_11111111", meetingPIN, at); err != nil {
		t.Fatal(err)
	}
	if err := s.CreateDialIn(t.Context(), "s_22222222", meetingPIN, at); !errors.Is(err, state.ErrPINTaken) {
		t.Fatalf("a PIN taken by a running session was given out twice: %v", err)
	}
	got, err := s.DialInByPIN(t.Context(), meetingPIN)
	if err != nil || got.SessionID != "s_11111111" || got.Room != "s_11111111" || got.Opened() || !got.CreatedAt.Equal(at) {
		t.Fatalf("by PIN: %+v %v", got, err)
	}
	if _, err := s.DialInByPIN(t.Context(), "48151624"); !errors.Is(err, state.ErrNotFound) {
		t.Errorf("a wrong PIN found a session: %v", err)
	}
	if pin, err := s.DialInPIN(t.Context(), "s_11111111"); err != nil || pin != meetingPIN {
		t.Errorf("disclosed PIN %q %v", pin, err)
	}

	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = db.Close() }()
	var index, sealed string
	if err := db.QueryRowContext(t.Context(), `SELECT pin_index, pin FROM dial_ins`).Scan(&index, &sealed); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(index+sealed, meetingPIN) || !strings.HasPrefix(sealed, "v1:") {
		t.Errorf("the PIN is stored in the clear: %q %q", index, sealed)
	}
}

func TestAllowedNumbersAreKeyedHashesReplacedWholeAndGoneWithTheDialIn(t *testing.T) {
	t.Parallel()
	path := filepath.Join(t.TempDir(), "dafter.db")
	s, err := state.Open(t.Context(), path)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	for _, id := range []string{"s_11111111", "s_22222222"} {
		dialInSession(t, s, id)
	}
	if err := s.SetDialInNumbers(t.Context(), "s_11111111", []string{allowed}); !errors.Is(err, state.ErrNotFound) {
		t.Fatalf("numbers were stored for a session without dial-in: %v", err)
	}
	for i, id := range []string{"s_11111111", "s_22222222"} {
		if err := s.CreateDialIn(t.Context(), id, meetingPIN[:7]+string(rune('0'+i)), time.Now()); err != nil {
			t.Fatal(err)
		}
		if err := s.SetDialInNumbers(t.Context(), id, []string{allowed, allowed}); err != nil {
			t.Fatal(err)
		}
	}
	if ok, err := s.DialInAllows(t.Context(), "s_11111111", allowed); !ok || err != nil {
		t.Errorf("allowed number refused: %v", err)
	}
	if ok, _ := s.DialInAllows(t.Context(), "s_11111111", stranger); ok {
		t.Error("a number never allowed was allowed")
	}
	if both, err := s.DialInsAllowing(t.Context(), allowed); err != nil || len(both) != 2 {
		t.Errorf("sessions allowing the number: %+v %v", both, err)
	}
	if err := s.SetDialInNumbers(t.Context(), "s_22222222", []string{stranger}); err != nil {
		t.Fatal(err)
	}
	if one, _ := s.DialInsAllowing(t.Context(), allowed); len(one) != 1 || one[0].SessionID != "s_11111111" {
		t.Errorf("a replaced list kept its old number: %+v", one)
	}

	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = db.Close() }()
	var dump string
	if err := db.QueryRowContext(t.Context(), `SELECT group_concat(number_index, ' ') FROM dial_in_numbers`).Scan(&dump); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(dump, "9876543210") || strings.Contains(dump, "9876500000") {
		t.Errorf("a number is stored in the clear: %s", dump)
	}

	if err := s.MarkDialInOpened(t.Context(), "s_11111111", time.Now()); err != nil {
		t.Fatal(err)
	}
	if err := s.EndDialIn(t.Context(), "s_11111111"); err != nil {
		t.Fatal(err)
	}
	if _, err := s.DialInPIN(t.Context(), "s_11111111"); !errors.Is(err, state.ErrNotFound) {
		t.Errorf("an ended dial-in kept its PIN: %v", err)
	}
	if ok, _ := s.DialInAllows(t.Context(), "s_11111111", allowed); ok {
		t.Error("an ended dial-in kept its numbers")
	}
	if left, _ := s.DialIns(t.Context()); len(left) != 1 || left[0].SessionID != "s_22222222" || left[0].Opened() {
		t.Errorf("dial-ins left: %+v", left)
	}
	if err := s.CreateDialIn(t.Context(), "s_11111111", meetingPIN[:7]+"0", time.Now()); err != nil {
		t.Errorf("an ended session's PIN is not free again: %v", err)
	}
}

func TestAnotherStateKeyDerivesOtherHashes(t *testing.T) {
	t.Parallel()
	path := filepath.Join(t.TempDir(), "dafter.db")
	open := func() *state.Store {
		k, err := state.EphemeralKeyCipher()
		if err != nil {
			t.Fatal(err)
		}
		s, err := state.Open(t.Context(), path, state.WithKeyCipher(k))
		if err != nil {
			t.Fatal(err)
		}
		return s
	}
	first := open()
	dialInSession(t, first, "s_11111111")
	if err := first.CreateDialIn(t.Context(), "s_11111111", meetingPIN, time.Now()); err != nil {
		t.Fatal(err)
	}
	if err := first.SetDialInNumbers(t.Context(), "s_11111111", []string{allowed}); err != nil {
		t.Fatal(err)
	}
	if err := first.Close(); err != nil {
		t.Fatal(err)
	}
	second := open()
	defer func() { _ = second.Close() }()
	if _, err := second.DialInByPIN(t.Context(), meetingPIN); !errors.Is(err, state.ErrNotFound) {
		t.Errorf("a PIN hashed under another state key still matched: %v", err)
	}
	if ok, _ := second.DialInAllows(t.Context(), "s_11111111", allowed); ok {
		t.Error("a number hashed under another state key still matched")
	}
}
