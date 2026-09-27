package state_test

import (
	"encoding/json"
	"errors"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/state"
)

func version(sessionID, hash string, createdAt time.Time) state.Transcript {
	return state.Transcript{
		SessionID: sessionID, TranscriptHash: hash, Provider: "sarvam", Model: "saaras:v3",
		CreatedAt: createdAt, StoredAt: createdAt.Add(time.Second),
		Document: json.RawMessage(`{"transcriptHash":"` + hash + `"}`),
	}
}

func TestAReRunIsANewVersionAndARetryIsNot(t *testing.T) {
	t.Parallel()
	s, sess := store(t), session(t)
	if err := s.CreateSession(t.Context(), sess); err != nil {
		t.Fatal(err)
	}
	at := time.Date(2026, 9, 24, 10, 31, 12, 4000, time.UTC)
	first, created, err := s.AddTranscript(t.Context(), version(sess.SessionID, "aa", at))
	if err != nil || !created || first.Version != 1 {
		t.Fatalf("first version: %+v created %v: %v", first, created, err)
	}
	retry, created, err := s.AddTranscript(t.Context(), version(sess.SessionID, "aa", at.Add(time.Hour)))
	if err != nil || created || retry.Version != 1 || !retry.CreatedAt.Equal(at) {
		t.Fatalf("a retried submission became %+v created %v: %v", retry, created, err)
	}
	rerun, created, err := s.AddTranscript(t.Context(), version(sess.SessionID, "bb", at.Add(time.Hour)))
	if err != nil || !created || rerun.Version != 2 {
		t.Fatalf("a re-run became %+v created %v: %v", rerun, created, err)
	}

	listed, err := s.Transcripts(t.Context(), sess.SessionID)
	if err != nil {
		t.Fatal(err)
	}
	if len(listed) != 2 || listed[0].Version != 1 || listed[1].Version != 2 || listed[0].Document != nil {
		t.Errorf("versions listed as %+v", listed)
	}
	got, err := s.Transcript(t.Context(), sess.SessionID, 1)
	if err != nil {
		t.Fatal(err)
	}
	if string(got.Document) != `{"transcriptHash":"aa"}` || got.TranscriptHash != "aa" || !got.CreatedAt.Equal(at) {
		t.Errorf("version 1 reads back as %+v", got)
	}
	if _, err := s.Transcript(t.Context(), sess.SessionID, 3); !errors.Is(err, state.ErrNoTranscript) {
		t.Errorf("an unknown version: %v", err)
	}
}

func TestATranscriptBelongsToAStoredSession(t *testing.T) {
	t.Parallel()
	s := store(t)
	if _, _, err := s.AddTranscript(t.Context(), version("s_00000000", "aa", time.Now())); err == nil {
		t.Error("a transcript was stored for a session nobody can explain")
	}
	if none, err := s.Transcripts(t.Context(), "s_00000000"); err != nil || len(none) != 0 {
		t.Errorf("an unknown session lists %+v (%v)", none, err)
	}
}
