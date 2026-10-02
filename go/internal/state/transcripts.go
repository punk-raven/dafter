package state

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
)

var ErrNoTranscript = errors.New("state: no such transcript version")

type Transcript struct {
	SessionID      string
	Version        int
	TranscriptHash string
	Provider       string
	Model          string
	CreatedAt      time.Time
	StoredAt       time.Time
	Document       json.RawMessage
}

const transcriptsMigration = `
CREATE TABLE IF NOT EXISTS transcripts (
	session_id      TEXT NOT NULL REFERENCES sessions(session_id),
	version         INTEGER NOT NULL,
	transcript_hash TEXT NOT NULL,
	provider        TEXT NOT NULL,
	model           TEXT NOT NULL,
	created_at      INTEGER NOT NULL,
	stored_at       INTEGER NOT NULL,
	document        TEXT NOT NULL,
	PRIMARY KEY (session_id, version),
	UNIQUE (session_id, transcript_hash)
) STRICT;
`

func (s *Store) AddTranscript(ctx context.Context, t Transcript) (Transcript, bool, error) {
	if existing, err := s.transcriptByHash(ctx, t.SessionID, t.TranscriptHash); err == nil {
		return existing, false, nil
	} else if !errors.Is(err, ErrNoTranscript) {
		return Transcript{}, false, err
	}
	row := s.db.QueryRowContext(ctx,
		`INSERT INTO transcripts (session_id, version, transcript_hash, provider, model, created_at, stored_at, document)
		 SELECT ?, COALESCE(MAX(version), 0) + 1, ?, ?, ?, ?, ?, ?
		 FROM transcripts WHERE session_id = ?
		 RETURNING version`,
		t.SessionID, t.TranscriptHash, t.Provider, t.Model,
		t.CreatedAt.UnixMicro(), t.StoredAt.UnixMicro(), string(t.Document), t.SessionID)
	if err := row.Scan(&t.Version); err != nil {
		return Transcript{}, false, errs.Wrap(errs.CodeInternal, err, "store transcript version")
	}
	t.CreatedAt, t.StoredAt = t.CreatedAt.Truncate(time.Microsecond).UTC(), t.StoredAt.Truncate(time.Microsecond).UTC()
	return t, true, nil
}

func (s *Store) transcriptByHash(ctx context.Context, sessionID, hash string) (Transcript, error) {
	return scanTranscript(s.db.QueryRowContext(ctx,
		`SELECT session_id, version, transcript_hash, provider, model, created_at, stored_at, document
		 FROM transcripts WHERE session_id = ? AND transcript_hash = ?`, sessionID, hash))
}

func (s *Store) Transcript(ctx context.Context, sessionID string, version int) (Transcript, error) {
	return scanTranscript(s.db.QueryRowContext(ctx,
		`SELECT session_id, version, transcript_hash, provider, model, created_at, stored_at, document
		 FROM transcripts WHERE session_id = ? AND version = ?`, sessionID, version))
}

func (s *Store) Transcripts(ctx context.Context, sessionID string) ([]Transcript, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT session_id, version, transcript_hash, provider, model, created_at, stored_at, ''
		 FROM transcripts WHERE session_id = ? ORDER BY version`, sessionID)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "list transcript versions")
	}
	defer func() { _ = rows.Close() }()
	var out []Transcript
	for rows.Next() {
		t, err := scanTranscript(rows)
		if err != nil {
			return nil, err
		}
		out = append(out, t)
	}
	if err := rows.Err(); err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "list transcript versions")
	}
	return out, nil
}

type scanner interface {
	Scan(dest ...any) error
}

func scanTranscript(row scanner) (Transcript, error) {
	var t Transcript
	var createdAt, storedAt int64
	var document string
	switch err := row.Scan(&t.SessionID, &t.Version, &t.TranscriptHash, &t.Provider, &t.Model,
		&createdAt, &storedAt, &document); {
	case errors.Is(err, sql.ErrNoRows):
		return Transcript{}, ErrNoTranscript
	case err != nil:
		return Transcript{}, errs.Wrap(errs.CodeInternal, err, "read transcript version")
	}
	t.CreatedAt, t.StoredAt = time.UnixMicro(createdAt).UTC(), time.UnixMicro(storedAt).UTC()
	if document != "" {
		t.Document = json.RawMessage(document)
	}
	return t, nil
}
