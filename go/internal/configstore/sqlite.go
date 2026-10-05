package configstore

import (
	"context"
	"database/sql"
	"errors"
	"time"

	_ "modernc.org/sqlite"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

const migration = `
CREATE TABLE IF NOT EXISTS config_revisions (
	revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
	kind        TEXT NOT NULL,
	name        TEXT NOT NULL,
	document    TEXT,
	actor       TEXT NOT NULL,
	note        TEXT NOT NULL DEFAULT '',
	created_at  INTEGER NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS config_revisions_by_name ON config_revisions(kind, name, revision_id);
CREATE TABLE IF NOT EXISTS config_releases (
	release_id       INTEGER PRIMARY KEY AUTOINCREMENT,
	actor            TEXT NOT NULL,
	note             TEXT NOT NULL DEFAULT '',
	created_at       INTEGER NOT NULL,
	rolled_back_from INTEGER NOT NULL DEFAULT 0
) STRICT;
CREATE TABLE IF NOT EXISTS config_release_members (
	release_id  INTEGER NOT NULL REFERENCES config_releases(release_id),
	revision_id INTEGER NOT NULL REFERENCES config_revisions(revision_id),
	PRIMARY KEY (release_id, revision_id)
) STRICT, WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS config_live (
	singleton  INTEGER PRIMARY KEY CHECK (singleton = 1),
	release_id INTEGER NOT NULL REFERENCES config_releases(release_id)
) STRICT;
CREATE TABLE IF NOT EXISTS config_changes (
	change_id   INTEGER PRIMARY KEY AUTOINCREMENT,
	at          INTEGER NOT NULL,
	actor       TEXT NOT NULL,
	action      TEXT NOT NULL,
	kind        TEXT NOT NULL DEFAULT '',
	name        TEXT NOT NULL DEFAULT '',
	revision_id INTEGER NOT NULL DEFAULT 0,
	release_id  INTEGER NOT NULL DEFAULT 0,
	note        TEXT NOT NULL DEFAULT ''
) STRICT;
`

var appendOnly = []string{"config_revisions", "config_releases", "config_release_members", "config_changes"}

type SQLite struct {
	db  *sql.DB
	now func() time.Time
}

func Open(ctx context.Context, path string) (*SQLite, error) {
	dsn := path + "?_pragma=foreign_keys(1)&_pragma=journal_mode(WAL)&_pragma=busy_timeout(5000)"
	db, err := sql.Open("sqlite", dsn)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "open config store")
	}
	if err := db.PingContext(ctx); err != nil {
		return nil, closing(db, errs.Wrap(errs.CodeInternal, err, "reach config store"))
	}
	if _, err := db.ExecContext(ctx, migration); err != nil {
		return nil, closing(db, errs.Wrap(errs.CodeInternal, err, "migrate config store"))
	}
	for _, table := range appendOnly {
		for _, op := range []string{"UPDATE", "DELETE"} {
			trigger := `CREATE TRIGGER IF NOT EXISTS ` + table + `_no_` + op + ` BEFORE ` + op + ` ON ` + table +
				` BEGIN SELECT RAISE(ABORT, 'config history is append-only'); END`
			if _, err := db.ExecContext(ctx, trigger); err != nil {
				return nil, closing(db, errs.Wrap(errs.CodeInternal, err, "migrate config store"))
			}
		}
	}
	return &SQLite{db: db, now: func() time.Time { return time.Now().UTC() }}, nil
}

func closing(db *sql.DB, err error) error {
	return errors.Join(err, db.Close())
}

func (s *SQLite) Close() error { return s.db.Close() }

func (s *SQLite) Put(ctx context.Context, w Write) (Revision, error) {
	if w.Action == "" {
		w.Action = ActionPut
		if w.Document == nil {
			w.Action = ActionDelete
		}
	}
	var rev Revision
	err := s.inTx(ctx, func(tx *sql.Tx) error {
		var err error
		rev, err = s.put(ctx, tx, w)
		return err
	})
	return rev, err
}

func (s *SQLite) put(ctx context.Context, tx *sql.Tx, w Write) (Revision, error) {
	at := s.now()
	var document any
	if w.Document != nil {
		document = string(w.Document)
	}
	res, err := tx.ExecContext(ctx,
		`INSERT INTO config_revisions (kind, name, document, actor, note, created_at) VALUES (?, ?, ?, ?, ?, ?)`,
		string(w.Kind), w.Name, document, w.Actor, w.Note, at.UnixMicro())
	if err != nil {
		return Revision{}, errs.Wrap(errs.CodeInternal, err, "store config revision")
	}
	id, err := res.LastInsertId()
	if err != nil {
		return Revision{}, errs.Wrap(errs.CodeInternal, err, "store config revision")
	}
	rev := Revision{ID: id, Kind: w.Kind, Name: w.Name, Document: w.Document, Deleted: w.Document == nil,
		Actor: w.Actor, Note: w.Note, CreatedAt: at}
	return rev, s.record(ctx, tx, Change{At: at, Actor: w.Actor, Action: w.Action, Kind: w.Kind, Name: w.Name, Revision: id, Note: w.Note})
}

func (s *SQLite) record(ctx context.Context, tx *sql.Tx, c Change) error {
	_, err := tx.ExecContext(ctx,
		`INSERT INTO config_changes (at, actor, action, kind, name, revision_id, release_id, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
		c.At.UnixMicro(), c.Actor, string(c.Action), string(c.Kind), c.Name, c.Revision, c.Release, c.Note)
	if err != nil {
		return errs.Wrap(errs.CodeInternal, err, "record config change")
	}
	return nil
}

const revisionColumns = `r.revision_id, r.kind, r.name, r.document, r.actor, r.note, r.created_at`

func (s *SQLite) Heads(ctx context.Context) ([]Revision, error) {
	return s.revisions(ctx, `SELECT `+revisionColumns+` FROM config_revisions r
		JOIN (SELECT MAX(revision_id) AS head FROM config_revisions GROUP BY kind, name) h ON r.revision_id = h.head
		WHERE r.document IS NOT NULL ORDER BY r.kind, r.name`)
}

func (s *SQLite) revisions(ctx context.Context, query string, args ...any) ([]Revision, error) {
	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "list config revisions")
	}
	defer func() { _ = rows.Close() }()
	var out []Revision
	for rows.Next() {
		var r Revision
		var kind string
		var document sql.NullString
		var createdAt int64
		if err := rows.Scan(&r.ID, &kind, &r.Name, &document, &r.Actor, &r.Note, &createdAt); err != nil {
			return nil, errs.Wrap(errs.CodeInternal, err, "read config revision")
		}
		r.Kind = config.Kind(kind)
		r.Deleted = !document.Valid
		if document.Valid {
			r.Document = []byte(document.String)
		}
		r.CreatedAt = time.UnixMicro(createdAt).UTC()
		out = append(out, r)
	}
	if err := rows.Err(); err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "list config revisions")
	}
	return out, nil
}

func (s *SQLite) inTx(ctx context.Context, fn func(*sql.Tx) error) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return errs.Wrap(errs.CodeInternal, err, "begin config transaction")
	}
	if err := fn(tx); err != nil {
		return errors.Join(err, tx.Rollback())
	}
	if err := tx.Commit(); err != nil {
		return errs.Wrap(errs.CodeInternal, err, "commit config transaction")
	}
	return nil
}
