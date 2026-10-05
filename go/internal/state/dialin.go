package state

import (
	"context"
	"database/sql"
	"errors"
	"strings"
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
)

const (
	pinPurpose    = "dial-in pin"
	numberPurpose = "dial-in number"
)

var ErrPINTaken = errors.New("state: that PIN opens another running session")

type DialIn struct {
	SessionID string
	Room      string
	CreatedAt time.Time
	OpenedAt  time.Time
}

func (d DialIn) Opened() bool { return !d.OpenedAt.IsZero() }

const dialInMigration = `
CREATE TABLE IF NOT EXISTS dial_ins (
	session_id TEXT PRIMARY KEY REFERENCES sessions(session_id),
	pin_index  TEXT NOT NULL UNIQUE,
	pin        TEXT NOT NULL,
	created_at INTEGER NOT NULL,
	opened_at  INTEGER
) STRICT;
CREATE TABLE IF NOT EXISTS dial_in_numbers (
	session_id   TEXT NOT NULL REFERENCES dial_ins(session_id) ON DELETE CASCADE,
	number_index TEXT NOT NULL,
	PRIMARY KEY (session_id, number_index)
) STRICT;
CREATE INDEX IF NOT EXISTS dial_in_numbers_by_number ON dial_in_numbers(number_index);
`

const dialInColumns = `SELECT d.session_id, s.room, d.created_at, d.opened_at FROM dial_ins d JOIN sessions s USING (session_id)`

func (s *Store) CreateDialIn(ctx context.Context, sessionID, pin string, at time.Time) error {
	sealed, err := s.keys.seal(sessionID, pin)
	if err != nil {
		return err
	}
	_, err = s.db.ExecContext(ctx,
		`INSERT INTO dial_ins (session_id, pin_index, pin, created_at) VALUES (?, ?, ?, ?)`,
		sessionID, s.keys.digest(pinPurpose, pin), sealed, at.UnixMicro())
	if err != nil && strings.Contains(err.Error(), "dial_ins.pin_index") {
		return ErrPINTaken
	}
	if err != nil {
		return errs.Wrap(errs.CodeInternal, err, "store dial-in")
	}
	return nil
}

func (s *Store) DialInPIN(ctx context.Context, sessionID string) (string, error) {
	var sealed string
	switch err := s.db.QueryRowContext(ctx, `SELECT pin FROM dial_ins WHERE session_id = ?`, sessionID).Scan(&sealed); {
	case errors.Is(err, sql.ErrNoRows):
		return "", ErrNotFound
	case err != nil:
		return "", errs.Wrap(errs.CodeInternal, err, "read dial-in")
	}
	return s.keys.open(sessionID, sealed)
}

func (s *Store) DialInByPIN(ctx context.Context, pin string) (DialIn, error) {
	found, err := s.dialIns(ctx, dialInColumns+` WHERE d.pin_index = ?`, s.keys.digest(pinPurpose, pin))
	if err != nil {
		return DialIn{}, err
	}
	if len(found) == 0 {
		return DialIn{}, ErrNotFound
	}
	return found[0], nil
}

func (s *Store) DialInsAllowing(ctx context.Context, number string) ([]DialIn, error) {
	return s.dialIns(ctx, dialInColumns+` JOIN dial_in_numbers n USING (session_id) WHERE n.number_index = ? ORDER BY d.session_id`,
		s.keys.digest(numberPurpose, number))
}

func (s *Store) DialIns(ctx context.Context) ([]DialIn, error) {
	return s.dialIns(ctx, dialInColumns+` ORDER BY d.session_id`)
}

func (s *Store) DialInAllows(ctx context.Context, sessionID, number string) (bool, error) {
	var n int
	err := s.db.QueryRowContext(ctx,
		`SELECT COUNT(*) FROM dial_in_numbers WHERE session_id = ? AND number_index = ?`,
		sessionID, s.keys.digest(numberPurpose, number)).Scan(&n)
	if err != nil {
		return false, errs.Wrap(errs.CodeInternal, err, "read dial-in numbers")
	}
	return n > 0, nil
}

func (s *Store) SetDialInNumbers(ctx context.Context, sessionID string, numbers []string) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return errs.Wrap(errs.CodeInternal, err, "store dial-in numbers")
	}
	defer func() { _ = tx.Rollback() }()
	var exists int
	if err := tx.QueryRowContext(ctx, `SELECT COUNT(*) FROM dial_ins WHERE session_id = ?`, sessionID).Scan(&exists); err != nil {
		return errs.Wrap(errs.CodeInternal, err, "store dial-in numbers")
	}
	if exists == 0 {
		return ErrNotFound
	}
	if _, err := tx.ExecContext(ctx, `DELETE FROM dial_in_numbers WHERE session_id = ?`, sessionID); err != nil {
		return errs.Wrap(errs.CodeInternal, err, "store dial-in numbers")
	}
	for _, number := range numbers {
		if _, err := tx.ExecContext(ctx,
			`INSERT OR IGNORE INTO dial_in_numbers (session_id, number_index) VALUES (?, ?)`,
			sessionID, s.keys.digest(numberPurpose, number)); err != nil {
			return errs.Wrap(errs.CodeInternal, err, "store dial-in numbers")
		}
	}
	if err := tx.Commit(); err != nil {
		return errs.Wrap(errs.CodeInternal, err, "store dial-in numbers")
	}
	return nil
}

func (s *Store) MarkDialInOpened(ctx context.Context, sessionID string, at time.Time) error {
	_, err := s.db.ExecContext(ctx,
		`UPDATE dial_ins SET opened_at = ? WHERE session_id = ? AND opened_at IS NULL`, at.UnixMicro(), sessionID)
	if err != nil {
		return errs.Wrap(errs.CodeInternal, err, "mark dial-in opened")
	}
	return nil
}

func (s *Store) EndDialIn(ctx context.Context, sessionID string) error {
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return errs.Wrap(errs.CodeInternal, err, "end dial-in")
	}
	defer func() { _ = tx.Rollback() }()
	for _, table := range []string{"dial_in_numbers", "dial_ins"} {
		if _, err := tx.ExecContext(ctx, `DELETE FROM `+table+` WHERE session_id = ?`, sessionID); err != nil {
			return errs.Wrap(errs.CodeInternal, err, "end dial-in")
		}
	}
	if err := tx.Commit(); err != nil {
		return errs.Wrap(errs.CodeInternal, err, "end dial-in")
	}
	return nil
}

func (s *Store) dialIns(ctx context.Context, query string, args ...any) ([]DialIn, error) {
	rows, err := s.db.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "read dial-ins")
	}
	defer func() { _ = rows.Close() }()
	var out []DialIn
	for rows.Next() {
		var d DialIn
		var createdAt int64
		var openedAt sql.NullInt64
		if err := rows.Scan(&d.SessionID, &d.Room, &createdAt, &openedAt); err != nil {
			return nil, errs.Wrap(errs.CodeInternal, err, "read dial-in")
		}
		d.CreatedAt = time.UnixMicro(createdAt).UTC()
		if openedAt.Valid {
			d.OpenedAt = time.UnixMicro(openedAt.Int64).UTC()
		}
		out = append(out, d)
	}
	if err := rows.Err(); err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "read dial-ins")
	}
	return out, nil
}
