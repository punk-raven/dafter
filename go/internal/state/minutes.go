package state

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
)

var ErrNoMinutes = errors.New("state: no minutes for this session")

type Minutes struct {
	SessionID string
	Version   int
	StoredAt  time.Time
	Document  json.RawMessage
}

const minutesMigration = `
CREATE TABLE IF NOT EXISTS minutes (
	session_id TEXT NOT NULL REFERENCES sessions(session_id),
	version    INTEGER NOT NULL,
	stored_at  INTEGER NOT NULL,
	document   TEXT NOT NULL,
	PRIMARY KEY (session_id, version)
) STRICT;
`

func (s *Store) AddMinutes(ctx context.Context, m Minutes) (Minutes, error) {
	row := s.db.QueryRowContext(ctx,
		`INSERT INTO minutes (session_id, version, stored_at, document)
		 SELECT ?, COALESCE(MAX(version), 0) + 1, ?, ?
		 FROM minutes WHERE session_id = ?
		 RETURNING version`,
		m.SessionID, m.StoredAt.UnixMicro(), string(m.Document), m.SessionID)
	if err := row.Scan(&m.Version); err != nil {
		return Minutes{}, errs.Wrap(errs.CodeInternal, err, "store minutes")
	}
	m.StoredAt = m.StoredAt.Truncate(time.Microsecond).UTC()
	return m, nil
}

func (s *Store) LatestMinutes(ctx context.Context, sessionID string) (Minutes, error) {
	var m Minutes
	var storedAt int64
	var document string
	switch err := s.db.QueryRowContext(ctx,
		`SELECT session_id, version, stored_at, document FROM minutes
		 WHERE session_id = ? ORDER BY version DESC LIMIT 1`, sessionID).
		Scan(&m.SessionID, &m.Version, &storedAt, &document); {
	case errors.Is(err, sql.ErrNoRows):
		return Minutes{}, ErrNoMinutes
	case err != nil:
		return Minutes{}, errs.Wrap(errs.CodeInternal, err, "read minutes")
	}
	m.StoredAt, m.Document = time.UnixMicro(storedAt).UTC(), json.RawMessage(document)
	return m, nil
}
