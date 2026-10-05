package state_test

import (
	"database/sql"
	"path/filepath"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/state"
)

func TestARecordingKeepsWhoseTrackItIs(t *testing.T) {
	t.Parallel()
	s, sess := store(t), session(t)
	if err := s.CreateSession(t.Context(), sess); err != nil {
		t.Fatal(err)
	}
	at := time.Date(2026, 9, 24, 10, 0, 1, 0, time.UTC)
	want := []state.Egress{
		{EgressID: "EG_human", SessionID: sess.SessionID, Layout: "track", StartedAt: at,
			TrackID: "TR_AMabc123", SpeakerKind: "human", ParticipantID: "p_4b81e0d7", Audio: true},
		{EgressID: "EG_agent", SessionID: sess.SessionID, Layout: "track", StartedAt: at.Add(time.Second),
			TrackID: "TR_AMagent1", SpeakerKind: "agent", Audio: true},
		{EgressID: "EG_room", SessionID: sess.SessionID, Layout: "room_composite", StartedAt: at.Add(2 * time.Second)},
	}
	for _, e := range want {
		if err := s.AddEgress(t.Context(), e); err != nil {
			t.Fatal(err)
		}
	}
	got, err := s.Egresses(t.Context(), sess.SessionID)
	if err != nil {
		t.Fatal(err)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Errorf("recording %d read back as %+v, want %+v", i, got[i], want[i])
		}
	}
}

func TestOpeningAnOlderStoreAddsTheTrackColumns(t *testing.T) {
	t.Parallel()
	path := filepath.Join(t.TempDir(), "dafter.db")
	db, err := sql.Open("sqlite", path)
	if err != nil {
		t.Fatal(err)
	}
	_, err = db.ExecContext(t.Context(), `
		CREATE TABLE sessions (
			session_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL, room TEXT NOT NULL,
			config_hash TEXT NOT NULL, config TEXT NOT NULL, created_at INTEGER NOT NULL
		) STRICT;
		CREATE TABLE egresses (
			egress_id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES sessions(session_id),
			layout TEXT NOT NULL, started_at INTEGER NOT NULL, stopped_at INTEGER
		) STRICT;
		INSERT INTO sessions VALUES ('s_7f3a9c21', 't_9c21a4be', 'room', 'hash', '{}', 0);
		INSERT INTO egresses VALUES ('EG_old', 's_7f3a9c21', 'track', 1, NULL);`)
	if err != nil {
		t.Fatal(err)
	}
	if err := db.Close(); err != nil {
		t.Fatal(err)
	}
	s, err := state.Open(t.Context(), path)
	if err != nil {
		t.Fatalf("open an older store: %v", err)
	}
	t.Cleanup(func() {
		if err := s.Close(); err != nil {
			t.Errorf("close store: %v", err)
		}
	})
	got, err := s.Egresses(t.Context(), "s_7f3a9c21")
	if err != nil {
		t.Fatalf("read a recording stored before the columns existed: %v", err)
	}
	if len(got) != 1 || got[0].TrackID != "" || got[0].SpeakerKind != "" || got[0].Audio {
		t.Errorf("an older recording reads back as %+v", got)
	}
}
