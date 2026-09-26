package control_test

import (
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/control"
	"github.com/punk-raven/dafter/go/internal/state"
	"github.com/punk-raven/dafter/go/internal/turn"
)

func TestNoICEServersWhenTURNNotConfigured(t *testing.T) {
	t.Parallel()
	h := serve(t)
	got := h.create(t, request("en-IN", "webrtc"))
	if got.ICEServers != nil {
		t.Errorf("expected no iceServers, got %v", got.ICEServers)
	}
}

func serveTURN(t *testing.T, turnServer *httptest.Server) *harness {
	t.Helper()
	raw, err := os.ReadFile(catalogPath)
	if err != nil {
		t.Fatalf("read catalog: %v", err)
	}
	catalog, err := config.LoadCatalog(raw)
	if err != nil {
		t.Fatalf("load catalog: %v", err)
	}
	store, err := state.Open(t.Context(), filepath.Join(t.TempDir(), "dafter.db"))
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() {
		if err := store.Close(); err != nil {
			t.Errorf("close store: %v", err)
		}
	})

	tport := &stubTransport{}
	turnFetcher := turn.NewFetcherWithClient("test-id", "test-token", turnServer.Client())
	turnFetcher.SetBaseURL(turnServer.URL)
	svc := &control.Service{
		Catalog: catalog, Store: store, Transport: tport,
		TURN: turnFetcher, TokenTTL: 15 * time.Minute,
	}
	server := httptest.NewServer(svc.Handler())
	t.Cleanup(server.Close)
	return &harness{server: server, store: store, transport: tport}
}

func TestICEServersReturnedWhenTURNConfigured(t *testing.T) {
	t.Parallel()

	turnServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"iceServers":[{"urls":["turn:turn.example.com:3478"],"username":"u","credential":"c"}]}`))
	}))
	t.Cleanup(turnServer.Close)

	h := serveTURN(t, turnServer)
	got := h.create(t, request("en-IN", "webrtc"))
	if len(got.ICEServers) != 1 {
		t.Fatalf("want 1 ice server, got %d", len(got.ICEServers))
	}
	if got.ICEServers[0].Username != "u" {
		t.Errorf("username = %q", got.ICEServers[0].Username)
	}
}

func TestSessionCreatedEvenWhenTURNFails(t *testing.T) {
	t.Parallel()

	turnServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"error":"service down"}`))
	}))
	t.Cleanup(turnServer.Close)

	h := serveTURN(t, turnServer)
	got := h.create(t, request("en-IN", "webrtc"))
	if got.SessionID == "" {
		t.Fatal("session was not created")
	}
	if got.ICEServers != nil {
		t.Errorf("expected no iceServers on TURN failure, got %v", got.ICEServers)
	}
}
