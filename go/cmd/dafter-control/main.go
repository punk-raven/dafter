package main

import (
	"context"
	"embed"
	"errors"
	"flag"
	"fmt"
	"io/fs"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/control"
	"github.com/punk-raven/dafter/go/internal/state"
	"github.com/punk-raven/dafter/go/internal/transport"
)

//go:embed all:catalog
var embedded embed.FS

func main() {
	if err := run(); err != nil {
		slog.Error("dafter-control stopped", "error", err)
		os.Exit(1)
	}
}

func run() error {
	addr := flag.String("addr", envOr("DAFTER_ADDR", "127.0.0.1:8080"), "listen address")
	dbPath := flag.String("db", envOr("DAFTER_DB", "dafter.db"), "SQLite path")
	catalogDir := flag.String("catalog", os.Getenv("DAFTER_CATALOG"), "config catalog directory; empty uses the embedded one")
	ttl := flag.Duration("token-ttl", transport.DefaultTTL, "join token lifetime")
	flag.Parse()

	lk, err := transport.NewLiveKit(
		envOr("DAFTER_LIVEKIT_URL", "ws://127.0.0.1:7880"),
		os.Getenv("DAFTER_LIVEKIT_API_KEY"),
		os.Getenv("DAFTER_LIVEKIT_API_SECRET"),
	)
	if err != nil {
		return fmt.Errorf("media transport: %w", err)
	}

	catalog, err := config.LoadCatalog(catalogFS(*catalogDir))
	if err != nil {
		return fmt.Errorf("config catalog: %w", err)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	store, err := state.Open(ctx, *dbPath)
	if err != nil {
		return fmt.Errorf("session store: %w", err)
	}
	defer func() {
		if err := store.Close(); err != nil {
			slog.Error("close session store", "error", err)
		}
	}()

	svc := &control.Service{Catalog: catalog, Store: store, Transport: lk, TokenTTL: *ttl}
	server := &http.Server{
		Addr:              *addr,
		Handler:           svc.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
	}

	go func() {
		<-ctx.Done()
		shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := server.Shutdown(shutdown); err != nil {
			slog.Error("shutdown", "error", err)
		}
	}()

	slog.Info("dafter-control listening", "addr", *addr, "db", *dbPath)
	if err := server.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}

func catalogFS(dir string) fs.FS {
	if dir != "" {
		return os.DirFS(dir)
	}
	sub, err := fs.Sub(embedded, "catalog")
	if err != nil {
		panic(err)
	}
	return sub
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
