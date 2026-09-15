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

func session(t *testing.T) state.Session {
	t.Helper()
	return state.Session{
		SessionID:  "s_7f3a9c21",
		TenantID:   "t_9c21a4be",
		Room:       "dafter-s_7f3a9c21",
		ConfigHash: "deadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
		Config:     json.RawMessage(storedDocument),
		CreatedAt:  time.Date(2026, 9, 15, 12, 34, 56, 789012000, time.UTC),
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
