package configstore

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

func (s *SQLite) Publish(ctx context.Context, p Publication) (Release, error) {
	if p.Action == "" {
		p.Action = ActionPublish
	}
	var rel Release
	err := s.inTx(ctx, func(tx *sql.Tx) error {
		if err := s.checkMembers(ctx, tx, p.Revisions); err != nil {
			return err
		}
		at := s.now()
		res, err := tx.ExecContext(ctx,
			`INSERT INTO config_releases (actor, note, created_at, rolled_back_from) VALUES (?, ?, ?, ?)`,
			p.Actor, p.Note, at.UnixMicro(), p.RolledBackFrom)
		if err != nil {
			return errs.Wrap(errs.CodeInternal, err, "store config release")
		}
		id, err := res.LastInsertId()
		if err != nil {
			return errs.Wrap(errs.CodeInternal, err, "store config release")
		}
		for _, rev := range p.Revisions {
			if _, err := tx.ExecContext(ctx,
				`INSERT INTO config_release_members (release_id, revision_id) VALUES (?, ?)`, id, rev); err != nil {
				return errs.Wrap(errs.CodeInternal, err, "store config release")
			}
		}
		if _, err := tx.ExecContext(ctx,
			`INSERT INTO config_live (singleton, release_id) VALUES (1, ?)
			 ON CONFLICT (singleton) DO UPDATE SET release_id = excluded.release_id`, id); err != nil {
			return errs.Wrap(errs.CodeInternal, err, "make config release live")
		}
		rel = Release{ID: id, Actor: p.Actor, Note: p.Note, CreatedAt: at, RolledBackFrom: p.RolledBackFrom, Live: true}
		return s.record(ctx, tx, Change{At: at, Actor: p.Actor, Action: p.Action, Release: id, Note: p.Note})
	})
	return rel, err
}

func (s *SQLite) checkMembers(ctx context.Context, tx *sql.Tx, revisions []int64) error {
	seen := map[string]bool{}
	for _, id := range revisions {
		var kind, name string
		var document sql.NullString
		err := tx.QueryRowContext(ctx,
			`SELECT kind, name, document FROM config_revisions WHERE revision_id = ?`, id).Scan(&kind, &name, &document)
		switch {
		case errors.Is(err, sql.ErrNoRows):
			return errs.Errorf(errs.CodeInvalidConfig, "a release names revision %d, which is not stored", id)
		case err != nil:
			return errs.Wrap(errs.CodeInternal, err, "read config revision")
		case !document.Valid:
			return errs.Errorf(errs.CodeInvalidConfig, "a release names revision %d, which deletes its document", id)
		case seen[kind+"/"+name]:
			return errs.Errorf(errs.CodeInvalidConfig, "a release names two revisions of one document")
		}
		seen[kind+"/"+name] = true
	}
	return nil
}

func (s *SQLite) LiveReleaseID(ctx context.Context) (int64, error) {
	var id int64
	err := s.db.QueryRowContext(ctx, `SELECT release_id FROM config_live WHERE singleton = 1`).Scan(&id)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		return 0, ErrNoRelease
	case err != nil:
		return 0, errs.Wrap(errs.CodeInternal, err, "read live config release")
	}
	return id, nil
}

func (s *SQLite) LiveRelease(ctx context.Context) (Release, error) {
	id, err := s.LiveReleaseID(ctx)
	if err != nil {
		return Release{}, err
	}
	return s.Release(ctx, id)
}

func (s *SQLite) Release(ctx context.Context, id int64) (Release, error) {
	rels, err := s.releases(ctx, `WHERE r.release_id = ?`, id)
	if err != nil {
		return Release{}, err
	}
	if len(rels) == 0 {
		return Release{}, ErrNotFound
	}
	rel := rels[0]
	rel.Revisions, err = s.revisions(ctx, `SELECT `+revisionColumns+` FROM config_revisions r
		JOIN config_release_members m ON m.revision_id = r.revision_id
		WHERE m.release_id = ? ORDER BY r.kind, r.name`, id)
	return rel, err
}

func (s *SQLite) Releases(ctx context.Context) ([]Release, error) {
	return s.releases(ctx, ``)
}

func (s *SQLite) releases(ctx context.Context, where string, args ...any) ([]Release, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT r.release_id, r.actor, r.note, r.created_at, r.rolled_back_from, COALESCE(l.release_id, 0)
		 FROM config_releases r LEFT JOIN config_live l ON l.release_id = r.release_id `+where+`
		 ORDER BY r.release_id DESC`, args...)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "list config releases")
	}
	defer func() { _ = rows.Close() }()
	var out []Release
	for rows.Next() {
		var r Release
		var createdAt, live int64
		if err := rows.Scan(&r.ID, &r.Actor, &r.Note, &createdAt, &r.RolledBackFrom, &live); err != nil {
			return nil, errs.Wrap(errs.CodeInternal, err, "read config release")
		}
		r.CreatedAt = time.UnixMicro(createdAt).UTC()
		r.Live = live != 0
		out = append(out, r)
	}
	if err := rows.Err(); err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "list config releases")
	}
	return out, nil
}

func (s *SQLite) History(ctx context.Context, limit int) ([]Change, error) {
	if limit <= 0 {
		limit = 100
	}
	rows, err := s.db.QueryContext(ctx,
		`SELECT change_id, at, actor, action, kind, name, revision_id, release_id, note
		 FROM config_changes ORDER BY change_id DESC LIMIT ?`, limit)
	if err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "list config changes")
	}
	defer func() { _ = rows.Close() }()
	var out []Change
	for rows.Next() {
		var c Change
		var at int64
		var action, kind string
		if err := rows.Scan(&c.ID, &at, &c.Actor, &action, &kind, &c.Name, &c.Revision, &c.Release, &c.Note); err != nil {
			return nil, errs.Wrap(errs.CodeInternal, err, "read config change")
		}
		c.At = time.UnixMicro(at).UTC()
		c.Action, c.Kind = Action(action), config.Kind(kind)
		out = append(out, c)
	}
	if err := rows.Err(); err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "list config changes")
	}
	return out, nil
}
