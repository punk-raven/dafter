package state

import (
	"context"
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
)

func instant(micros int64) time.Time {
	if micros == 0 {
		return time.Time{}
	}
	return time.UnixMicro(micros).UTC()
}

func (s *Store) MarkJoined(ctx context.Context, sessionID string, at time.Time) error {
	res, err := s.db.ExecContext(ctx,
		`UPDATE sessions SET joined_at = ? WHERE session_id = ? AND joined_at = 0`,
		at.UnixMicro(), sessionID)
	if err != nil {
		return errs.Wrap(errs.CodeInternal, err, "mark session joined")
	}
	if n, err := res.RowsAffected(); err == nil && n == 0 {
		if _, err := s.Session(ctx, sessionID); err != nil {
			return err
		}
	}
	return nil
}

func (s *Store) EndSession(ctx context.Context, sessionID string, at time.Time) (bool, error) {
	res, err := s.db.ExecContext(ctx,
		`UPDATE sessions SET ended_at = ? WHERE session_id = ? AND joined_at != 0 AND ended_at = 0`,
		at.UnixMicro(), sessionID)
	if err != nil {
		return false, errs.Wrap(errs.CodeInternal, err, "end session")
	}
	n, err := res.RowsAffected()
	if err != nil {
		return false, errs.Wrap(errs.CodeInternal, err, "end session")
	}
	return n == 1, nil
}
