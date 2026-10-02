package main

import (
	"context"
	"embed"
	"errors"
	"flag"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/control"
	"github.com/punk-raven/dafter/go/internal/state"
	"github.com/punk-raven/dafter/go/internal/transport"
	"github.com/punk-raven/dafter/go/internal/turn"
)

//go:embed catalog.json
var embeddedCatalog []byte

//go:embed testclient.html
var testClientHTML []byte

//go:embed agent.js agent-call.js agent-turns.js agent-refusal.js agent.css
var clientAssets embed.FS

var clientAssetPaths = map[string]string{
	"/agent.js":         "agent.js",
	"/agent-call.js":    "agent-call.js",
	"/agent-turns.js":   "agent-turns.js",
	"/agent-refusal.js": "agent-refusal.js",
	"/agent.css":        "agent.css",
}

func main() {
	if err := run(); err != nil {
		slog.Error("dafter-control stopped", "error", err)
		os.Exit(1)
	}
}

func run() error {
	addr := flag.String("addr", envOr("DAFTER_ADDR", "127.0.0.1:8080"), "listen address")
	dbPath := flag.String("db", envOr("DAFTER_DB", "dafter.db"), "SQLite path")
	catalogPath := flag.String("catalog", os.Getenv("DAFTER_CATALOG"), "config catalog file; empty uses the embedded one")
	ttl := flag.Duration("token-ttl", transport.DefaultTTL, "join token lifetime")
	flag.Parse()

	lk, err := transport.NewLiveKit(
		envOr("DAFTER_LIVEKIT_URL", "ws://127.0.0.1:7880"),
		os.Getenv("DAFTER_LIVEKIT_API_KEY"),
		os.Getenv("DAFTER_LIVEKIT_API_SECRET"),
		egressOptions()...,
	)
	if err != nil {
		return fmt.Errorf("media transport: %w", err)
	}

	raw, err := catalogBytes(*catalogPath)
	if err != nil {
		return err
	}
	catalog, err := config.LoadCatalog(raw)
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

	turnFetcher := turn.NewFetcher(
		os.Getenv("DAFTER_TURN_TOKEN_ID"),
		os.Getenv("DAFTER_TURN_API_TOKEN"),
	)
	if turnFetcher.Enabled() {
		slog.Info("cloudflare TURN credentials enabled")
	}

	svc := &control.Service{
		Catalog: catalog, Store: store, Transport: lk, TURN: turnFetcher, TokenTTL: *ttl,
		WorkerSecret: os.Getenv("DAFTER_WORKER_SECRET"),
	}
	if svc.WorkerSecret == "" {
		slog.Info("worker calls disabled: DAFTER_WORKER_SECRET is not set, so no agent can join an end-to-end session")
	}
	handler := svc.MetricsHandler()
	metricsHandler := promhttp.Handler()
	server := &http.Server{
		Addr: *addr,
		Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if r.Method == http.MethodGet && r.URL.Path == "/" {
				w.Header().Set("Content-Type", "text/html; charset=utf-8")
				if _, err := w.Write(testClientHTML); err != nil {
					slog.Error("write test client", "error", err)
				}
				return
			}
			if name, ok := clientAssetPaths[r.URL.Path]; ok && r.Method == http.MethodGet {
				http.ServeFileFS(w, r, clientAssets, name)
				return
			}
			if r.Method == http.MethodGet && r.URL.Path == "/metrics" {
				metricsHandler.ServeHTTP(w, r)
				return
			}
			handler.ServeHTTP(w, r)
		}),
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

func egressOptions() []transport.Option {
	bucket := os.Getenv("DAFTER_EGRESS_S3_BUCKET")
	if bucket == "" {
		slog.Info("recording disabled: DAFTER_EGRESS_S3_BUCKET is not set")
		return nil
	}
	storage := transport.EgressStorage{
		Bucket:         bucket,
		Endpoint:       os.Getenv("DAFTER_EGRESS_S3_ENDPOINT"),
		Region:         os.Getenv("DAFTER_EGRESS_S3_REGION"),
		AccessKey:      os.Getenv("DAFTER_EGRESS_S3_ACCESS_KEY"),
		Secret:         os.Getenv("DAFTER_EGRESS_S3_SECRET"),
		ForcePathStyle: os.Getenv("DAFTER_EGRESS_S3_FORCE_PATH_STYLE") == "true",
	}
	slog.Info("recording enabled", "bucket", bucket, "endpoint", storage.Endpoint)
	return []transport.Option{transport.WithEgressStorage(storage)}
}

func catalogBytes(path string) ([]byte, error) {
	if path == "" {
		return embeddedCatalog, nil
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read config catalog: %w", err)
	}
	return raw, nil
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
