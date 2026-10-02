package configstore

import (
	"context"
	"errors"
	"log/slog"
	"sync"
	"sync/atomic"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/configcheck"
	"github.com/punk-raven/dafter/go/internal/errs"
)

type Live struct {
	store   Store
	log     *slog.Logger
	current atomic.Pointer[config.Snapshot]

	mu         sync.Mutex
	lastFailed int64
}

func NewLive(ctx context.Context, store Store, log *slog.Logger) (*Live, error) {
	if log == nil {
		log = slog.Default()
	}
	l := &Live{store: store, log: log}
	if err := l.Refresh(ctx); err != nil {
		return nil, err
	}
	return l, nil
}

func (l *Live) Snapshot() config.Snapshot {
	return *l.current.Load()
}

func (l *Live) Refresh(ctx context.Context) error {
	l.mu.Lock()
	defer l.mu.Unlock()
	id, err := l.store.LiveReleaseID(ctx)
	if err != nil {
		return err
	}
	if current := l.current.Load(); current != nil && current.Release == id {
		return nil
	}
	rel, err := l.store.Release(ctx, id)
	if err != nil {
		return err
	}
	catalog, err := configcheck.All(Documents(rel.Revisions))
	if err != nil {
		refused := errs.Wrap(errs.CodeInvalidConfig, err, "release %d does not load, so the running catalog stays as it was", id)
		var de *errs.Error
		if errors.As(err, &de) {
			refused.Details = de.Details
		}
		return refused
	}
	previous := l.current.Swap(&config.Snapshot{Catalog: catalog, Release: id})
	if previous != nil {
		l.log.Info("config release swapped in", "release", id, "previous", previous.Release)
	}
	return nil
}

func (l *Live) Watch(ctx context.Context, every time.Duration) {
	ticker := time.NewTicker(every)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			l.refreshLogged(ctx)
		}
	}
}

func (l *Live) refreshLogged(ctx context.Context) {
	err := l.Refresh(ctx)
	if err == nil {
		return
	}
	id, _ := l.store.LiveReleaseID(ctx)
	l.mu.Lock()
	defer l.mu.Unlock()
	if id != l.lastFailed {
		l.lastFailed = id
		l.log.Error("config release not loaded", "release", id, "error", err)
	}
}
