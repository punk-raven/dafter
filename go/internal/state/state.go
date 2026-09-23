package state

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"time"

	_ "modernc.org/sqlite"

	"github.com/punk-raven/dafter/go/internal/errs"
)

type Session struct {
	SessionID  string
	TenantID   string
	Room       string
	ConfigHash string
	Config     json.RawMessage
	CreatedAt  time.Time

	EncryptionKey string
}

type Egress struct {
	EgressID  string
	SessionID string
	Layout    string
	StartedAt time.Time
	StoppedAt time.Time
}

func (e Egress) Active() bool { return e.StoppedAt.IsZero() }

var ErrNotFound = errors.New("state: no such session")

type SessionStore interface {
	CreateSession(ctx context.Context, sess Session) error
	Session(ctx context.Context, sessionID string) (Session, error)
	AddEgress(ctx context.Context, e Egress) error
	StopEgress(ctx context.Context, egressID string, at time.Time) error
	Egresses(ctx context.Context, sessionID string) ([]Egress, error)
	Close() error
}

type Store struct {
	db *sql.DB
}

const migration = `
CREATE TABLE IF NOT EXISTS sessions (
	session_id     TEXT PRIMARY KEY,
	tenant_id      TEXT NOT NULL,
	room           TEXT NOT NULL,
	config_hash    TEXT NOT NULL,
	config         TEXT NOT NULL,
	created_at     INTEGER NOT NULL,
	encryption_key TEXT NOT NULL DEFAULT ''
) STRICT;
CREATE TABLE IF NOT EXISTS egresses (
	egress_id   TEXT PRIMARY KEY,
	session_id  TEXT NOT NULL REFERENCES sessions(session_id),
	layout      TEXT NOT NULL,
	started_at  INTEGER NOT NULL,
	stopped_at  INTEGER
) STRICT;
CREATE INDEX IF NOT EXISTS egresses_by_session ON egresses(session_id, started_at);
`

var addedColumns = []struct{ name, definition string }{
	{"encryption_key", "TEXT NOT NULL DEFAULT ''"},
}

func Open(ctx context.Context, path string) (*Store, error) {
	dsn := path + "?_pragma=foreign_keys(1)&_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "open session store")
	}
	if err := db.PingContext(ctx); err != nil {
		return nil, closing(db, errs.Wrap(errs.CodeInternal, err, "reach session store"))
	}
	if _, err := db.ExecContext(ctx, migration); err != nil {
		return nil, closing(db, errs.Wrap(errs.CodeInternal, err, "migrate session store"))
	}
	if err := addMissingColumns(ctx, db); err != nil {
		return nil, closing(db, err)
	}
	return &Store{db: db}, nil
}

func addMissingColumns(ctx context.Context, db *sql.DB) error {
	rows, err := db.QueryContext(ctx, `SELECT name FROM pragma_table_info('sessions')`)
	if err != nil {
		return errs.Wrap(errs.CodeInternal, err, "inspect session store")
	}
	present := map[string]bool{}
	for rows.Next() {
		var name string
		if err := rows.Scan(&name); err != nil {
			return errors.Join(errs.Wrap(errs.CodeInternal, err, "inspect session store"), rows.Close())
		}
		present[name] = true
	}
	if err := errors.Join(rows.Err(), rows.Close()); err != nil {
		return errs.Wrap(errs.CodeInternal, err, "inspect session store")
	}
	for _, c := range addedColumns {
		if present[c.name] {
			continue
		}
		if _, err := db.ExecContext(ctx, `ALTER TABLE sessions ADD COLUMN `+c.name+` `+c.definition); err != nil {
			return errs.Wrap(errs.CodeInternal, err, "migrate session store")
		}
	}
	return nil
}

func closing(db *sql.DB, err error) error {
	return errors.Join(err, db.Close())
}

func (s *Store) Close() error { return s.db.Close() }

func (s *Store) CreateSession(ctx context.Context, sess Session) error {
	_, err := s.db.ExecContext(ctx,
		`INSERT INTO sessions (session_id, tenant_id, room, config_hash, config, created_at, encryption_key)
		 VALUES (?, ?, ?, ?, ?, ?, ?)`,
		sess.SessionID, sess.TenantID, sess.Room, sess.ConfigHash,
		string(sess.Config), sess.CreatedAt.UnixMicro(), sess.EncryptionKey)
	if err != nil {
		return errs.Wrap(errs.CodeInternal, err, "store session")
	}
	return nil
}

func (s *Store) Session(ctx context.Context, sessionID string) (Session, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT session_id, tenant_id, room, config_hash, config, created_at, encryption_key
		 FROM sessions WHERE session_id = ?`, sessionID)

	var sess Session
	var config string
	var createdAt int64
	switch err := row.Scan(&sess.SessionID, &sess.TenantID, &sess.Room,
		&sess.ConfigHash, &config, &createdAt, &sess.EncryptionKey); {
	case errors.Is(err, sql.ErrNoRows):
		return Session{}, ErrNotFound
	case err != nil:
		return Session{}, errs.Wrap(errs.CodeInternal, err, "read session")
	}

	sess.Config = json.RawMessage(config)
	sess.CreatedAt = time.UnixMicro(createdAt).UTC()
	return sess, nil
}

func (s *Store) AddEgress(ctx context.Context, e Egress) error {
	_, err := s.db.ExecContext(ctx,
		`INSERT INTO egresses (egress_id, session_id, layout, started_at, stopped_at)
		 VALUES (?, ?, ?, ?, NULL)`,
		e.EgressID, e.SessionID, e.Layout, e.StartedAt.UnixMicro())
	if err != nil {
		return errs.Wrap(errs.CodeInternal, err, "store egress")
	}
	return nil
}

func (s *Store) StopEgress(ctx context.Context, egressID string, at time.Time) error {
	res, err := s.db.ExecContext(ctx,
		`UPDATE egresses SET stopped_at = ? WHERE egress_id = ? AND stopped_at IS NULL`,
		at.UnixMicro(), egressID)
	if err != nil {
		return errs.Wrap(errs.CodeInternal, err, "mark egress stopped")
	}
	if n, err := res.RowsAffected(); err == nil && n == 0 {
		return ErrNotFound
	}
	return nil
}

func (s *Store) Egresses(ctx context.Context, sessionID string) ([]Egress, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT egress_id, session_id, layout, started_at, stopped_at
		 FROM egresses WHERE session_id = ? ORDER BY started_at, egress_id`, sessionID)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "list egresses")
	}
	defer func() { _ = rows.Close() }()

	var out []Egress
	for rows.Next() {
		var e Egress
		var startedAt int64
		var stoppedAt sql.NullInt64
		if err := rows.Scan(&e.EgressID, &e.SessionID, &e.Layout, &startedAt, &stoppedAt); err != nil {
			return nil, errs.Wrap(errs.CodeInternal, err, "read egress")
		}
		e.StartedAt = time.UnixMicro(startedAt).UTC()
		if stoppedAt.Valid {
			e.StoppedAt = time.UnixMicro(stoppedAt.Int64).UTC()
		}
		out = append(out, e)
	}
	if err := rows.Err(); err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "list egresses")
	}
	return out, nil
}
