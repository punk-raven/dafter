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
}

var ErrNotFound = errors.New("state: no such session")

type Store struct {
	db *sql.DB
}

const migration = `
CREATE TABLE IF NOT EXISTS sessions (
	session_id  TEXT PRIMARY KEY,
	tenant_id   TEXT NOT NULL,
	room        TEXT NOT NULL,
	config_hash TEXT NOT NULL,
	config      TEXT NOT NULL,
	created_at  TEXT NOT NULL
) STRICT;
`

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
	return &Store{db: db}, nil
}

func closing(db *sql.DB, err error) error {
	return errors.Join(err, db.Close())
}

func (s *Store) Close() error { return s.db.Close() }

const rfc3339Micro = "2006-01-02T15:04:05.000000Z07:00"

func (s *Store) CreateSession(ctx context.Context, sess Session) error {
	_, err := s.db.ExecContext(ctx,
		`INSERT INTO sessions (session_id, tenant_id, room, config_hash, config, created_at)
		 VALUES (?, ?, ?, ?, ?, ?)`,
		sess.SessionID, sess.TenantID, sess.Room, sess.ConfigHash,
		string(sess.Config), sess.CreatedAt.UTC().Format(rfc3339Micro))
	if err != nil {
		return errs.Wrap(errs.CodeInternal, err, "store session")
	}
	return nil
}

func (s *Store) Session(ctx context.Context, sessionID string) (Session, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT session_id, tenant_id, room, config_hash, config, created_at
		 FROM sessions WHERE session_id = ?`, sessionID)

	var sess Session
	var config, createdAt string
	switch err := row.Scan(&sess.SessionID, &sess.TenantID, &sess.Room,
		&sess.ConfigHash, &config, &createdAt); {
	case errors.Is(err, sql.ErrNoRows):
		return Session{}, ErrNotFound
	case err != nil:
		return Session{}, errs.Wrap(errs.CodeInternal, err, "read session")
	}

	sess.Config = json.RawMessage(config)
	t, err := time.Parse(rfc3339Micro, createdAt)
	if err != nil {
		return Session{}, errs.Wrap(errs.CodeInternal, err, "decode stored timestamp")
	}
	sess.CreatedAt = t
	return sess, nil
}
