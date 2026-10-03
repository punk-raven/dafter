package main

import (
	"context"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"time"

	"github.com/punk-raven/dafter/go/internal/admin"
	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/configstore"
	"github.com/punk-raven/dafter/go/internal/state"
)

type configStore struct {
	store     *configstore.SQLite
	editor    *configstore.Editor
	live      *configstore.Live
	adminAddr string
}

func openConfigStore(ctx context.Context, path string, catalog *config.Catalog, poll time.Duration, adminAddr string) (*configStore, error) {
	store, err := configstore.Open(ctx, path)
	if err != nil {
		return nil, fmt.Errorf("config store: %w", err)
	}
	c := &configStore{store: store, editor: &configstore.Editor{Store: store}, adminAddr: adminAddr}
	docs, err := catalog.Documents()
	if err != nil {
		return nil, errors.Join(err, store.Close())
	}
	rel, outcome, err := c.editor.Seed(ctx, docs)
	if err != nil {
		return nil, errors.Join(fmt.Errorf("config store: import catalog: %w", err), store.Close())
	}
	slog.Info("config store ready", "catalog", outcome, "live release", rel.ID)
	if c.live, err = configstore.NewLive(ctx, store, slog.Default()); err != nil {
		return nil, errors.Join(fmt.Errorf("config store: load live release: %w", err), store.Close())
	}
	go c.live.Watch(ctx, poll)
	return c, nil
}

func (c *configStore) close() {
	if err := c.store.Close(); err != nil {
		slog.Error("close config store", "error", err)
	}
}

func (c *configStore) serveAdmin(ctx context.Context) {
	token := os.Getenv("DAFTER_ADMIN_TOKEN")
	if token == "" {
		slog.Info("admin API disabled: DAFTER_ADMIN_TOKEN is not set")
		return
	}
	api, err := admin.New(c.editor, c.live, token, slog.Default())
	if err != nil {
		slog.Error("admin API disabled", "error", err)
		return
	}
	server := &http.Server{Addr: c.adminAddr, Handler: adminHandler(api.Handler()), ReadHeaderTimeout: 5 * time.Second}
	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdown); err != nil {
			slog.Error("admin shutdown", "error", err)
		}
	}()
	go func() {
		slog.Info("admin API and panel listening", "addr", c.adminAddr, "panel", "http://"+c.adminAddr+"/")
		if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			slog.Error("admin API stopped", "error", err)
		}
	}()
}

func sessionKeyCipher() (*state.KeyCipher, error) {
	encoded := os.Getenv("DAFTER_STATE_KEY")
	if encoded == "" {
		slog.Warn("DAFTER_STATE_KEY is not set: end-to-end session keys are sealed at rest with a key that lives only in this process, so they cannot be read after a restart")
		return state.EphemeralKeyCipher()
	}
	keys, err := state.ParseKeyCipher(encoded)
	if err != nil {
		return nil, fmt.Errorf("DAFTER_STATE_KEY: %w", err)
	}
	return keys, nil
}
