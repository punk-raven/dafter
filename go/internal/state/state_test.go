package state_test

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/state"
)

func store(t *testing.T) *state.Store {
	t.Helper()
	s, err := state.Open(t.Context(), filepath.Join(t.TempDir(), "dafter.db"))
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() {
		if err := s.Close(); err != nil {
			t.Errorf("close store: %v", err)
		}
	})
	return s
}

// The store keeps bytes and never reads them, so this is the test's own
// document and not the shared cross-language vector, which belongs to the
// config and hashing tests. It is spelled awkwardly on purpose: a store that
// tidies what it was handed breaks the hash taken before the bytes arrived.
const storedDocument = `{"b": "\u00e5 \u0906",
  "a": [1, 2.50, null, "</script>"],
  "nested": {"quote": "\"", "slash": "a/b", "": "empty key"}}`

var stampedIn = time.FixedZone("+05:30", 5*60*60+30*60)

func session(t *testing.T) state.Session {
	t.Helper()
	return state.Session{
		SessionID:  "s_7f3a9c21",
		TenantID:   "t_9c21a4be",
		Room:       "dafter-s_7f3a9c21",
		ConfigHash: "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
		Config:     json.RawMessage(storedDocument),
		CreatedAt:  time.Date(2026, 9, 15, 18, 4, 56, 789012000, stampedIn),
	}
}

func TestSessionRoundTripsTheResolvedDocument(t *testing.T) {
	t.Parallel()
	s := store(t)
	want := session(t)
	if err := s.CreateSession(t.Context(), want); err != nil {
		t.Fatalf("create session: %v", err)
	}

	got, err := s.Session(t.Context(), want.SessionID)
	if err != nil {
		t.Fatalf("read session: %v", err)
	}
	if !bytes.Equal(got.Config, want.Config) {
		t.Error("the stored document came back changed; its hash would no longer verify")
	}
	if got.ConfigHash != want.ConfigHash || got.Room != want.Room || got.TenantID != want.TenantID {
		t.Errorf("session came back as %+v", got)
	}
	if !got.CreatedAt.Equal(want.CreatedAt) {
		t.Errorf("created at %s, want %s", got.CreatedAt, want.CreatedAt)
	}
	if got.CreatedAt.Location() != time.UTC {
		t.Errorf("created at came back in %s; a stored instant reads back as UTC, or every caller normalizes it instead", got.CreatedAt.Location())
	}
}

func TestAnUnknownSessionIsNotFound(t *testing.T) {
	t.Parallel()
	if _, err := store(t).Session(t.Context(), "s_00000000"); !errors.Is(err, state.ErrNotFound) {
		t.Fatalf("want %v, got %v", state.ErrNotFound, err)
	}
}

func TestASessionIDIsClaimedOnce(t *testing.T) {
	t.Parallel()
	s, sess := store(t), session(t)
	if err := s.CreateSession(t.Context(), sess); err != nil {
		t.Fatal(err)
	}
	if err := s.CreateSession(t.Context(), sess); err == nil {
		t.Fatal("a second session reused the id; the system of record would hold two truths for one session")
	}
}

func TestTheStoreSurvivesReopening(t *testing.T) {
	t.Parallel()
	path := filepath.Join(t.TempDir(), "dafter.db")
	first, err := state.Open(t.Context(), path)
	if err != nil {
		t.Fatal(err)
	}
	sess := session(t)
	if err := first.CreateSession(t.Context(), sess); err != nil {
		t.Fatal(err)
	}
	if err := first.Close(); err != nil {
		t.Fatal(err)
	}

	second, err := state.Open(context.Background(), path)
	if err != nil {
		t.Fatalf("reopen: %v", err)
	}
	t.Cleanup(func() {
		if err := second.Close(); err != nil {
			t.Errorf("close store: %v", err)
		}
	})
	got, err := second.Session(t.Context(), sess.SessionID)
	if err != nil {
		t.Fatalf("read after reopen: %v", err)
	}
	if got.ConfigHash != sess.ConfigHash {
		t.Errorf("hash came back %q after reopening", got.ConfigHash)
	}
}

func TestEgressesAreKeptPerSessionInStartOrder(t *testing.T) {
	t.Parallel()
	s, sess := store(t), session(t)
	if err := s.CreateSession(t.Context(), sess); err != nil {
		t.Fatal(err)
	}
	first := state.Egress{EgressID: "EG_first", SessionID: sess.SessionID, Layout: "track",
		StartedAt: time.Date(2026, 9, 15, 18, 5, 0, 0, time.UTC)}
	second := state.Egress{EgressID: "EG_second", SessionID: sess.SessionID, Layout: "track",
		StartedAt: first.StartedAt.Add(time.Second)}
	for _, e := range []state.Egress{second, first} {
		if err := s.AddEgress(t.Context(), e); err != nil {
			t.Fatalf("add egress: %v", err)
		}
	}

	stoppedAt := second.StartedAt.Add(time.Minute)
	if err := s.StopEgress(t.Context(), first.EgressID, stoppedAt); err != nil {
		t.Fatalf("stop egress: %v", err)
	}

	got, err := s.Egresses(t.Context(), sess.SessionID)
	if err != nil {
		t.Fatalf("list egresses: %v", err)
	}
	if len(got) != 2 || got[0].EgressID != first.EgressID || got[1].EgressID != second.EgressID {
		t.Fatalf("egresses came back as %+v; a reader expects start order", got)
	}
	if got[0].Active() || !got[0].StoppedAt.Equal(stoppedAt) {
		t.Errorf("the stopped egress reads back as %+v", got[0])
	}
	if !got[1].Active() {
		t.Errorf("the running egress reads back as stopped: %+v", got[1])
	}
	if got[1].StartedAt.Location() != time.UTC {
		t.Errorf("started at came back in %s", got[1].StartedAt.Location())
	}
}

func TestAnEgressIsStoppedOnceAndBelongsToAStoredSession(t *testing.T) {
	t.Parallel()
	s, sess := store(t), session(t)
	if err := s.CreateSession(t.Context(), sess); err != nil {
		t.Fatal(err)
	}
	orphan := state.Egress{EgressID: "EG_orphan", SessionID: "s_00000000", Layout: "track", StartedAt: time.Now()}
	if err := s.AddEgress(t.Context(), orphan); err == nil {
		t.Error("an egress was recorded for a session nobody can explain")
	}

	e := state.Egress{EgressID: "EG_once", SessionID: sess.SessionID, Layout: "room_composite", StartedAt: time.Now()}
	if err := s.AddEgress(t.Context(), e); err != nil {
		t.Fatal(err)
	}
	if err := s.StopEgress(t.Context(), e.EgressID, time.Now()); err != nil {
		t.Fatal(err)
	}
	if err := s.StopEgress(t.Context(), e.EgressID, time.Now()); !errors.Is(err, state.ErrNotFound) {
		t.Errorf("a second stop moved the stop time: %v", err)
	}
	if err := s.StopEgress(t.Context(), "EG_unknown", time.Now()); !errors.Is(err, state.ErrNotFound) {
		t.Errorf("stopping an unknown egress: want %v, got %v", state.ErrNotFound, err)
	}

	none, err := s.Egresses(t.Context(), "s_00000000")
	if err != nil || len(none) != 0 {
		t.Errorf("an unknown session lists %v, %v", none, err)
	}
}
