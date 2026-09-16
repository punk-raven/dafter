package turn_test

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"github.com/punk-raven/dafter/go/internal/turn"
)

func TestFetchCredentialsReturnsICEServers(t *testing.T) {
	t.Parallel()

	want := []turn.ICEServer{{
		URLs:       []string{"stun:stun.cloudflare.com:3478", "turn:turn.cloudflare.com:3478?transport=udp"},
		Username:   "test-user",
		Credential: "test-cred",
	}}

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			t.Errorf("want POST, got %s", r.Method)
		}
		if got := r.Header.Get("Authorization"); got != "Bearer test-api-token" {
			t.Errorf("auth header = %q", got)
		}

		var body struct{ TTL int }
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("decode request: %v", err)
		}
		if body.TTL != 86400 {
			t.Errorf("ttl = %d, want 86400", body.TTL)
		}

		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusCreated)
		if err := json.NewEncoder(w).Encode(map[string]any{"iceServers": want}); err != nil {
			t.Errorf("encode response: %v", err)
		}
	}))
	t.Cleanup(server.Close)

	f := turn.NewFetcherWithClient("test-token-id", "test-api-token", server.Client())
	f.SetBaseURL(server.URL)

	got, err := f.FetchCredentials(t.Context())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(got) != 1 {
		t.Fatalf("got %d ice servers, want 1", len(got))
	}
	if got[0].Username != "test-user" || got[0].Credential != "test-cred" {
		t.Errorf("credentials = %+v", got[0])
	}
	if len(got[0].URLs) != 2 {
		t.Errorf("urls = %v", got[0].URLs)
	}
}

func TestFetchCredentialsDisabledReturnsNil(t *testing.T) {
	t.Parallel()

	f := turn.NewFetcher("", "")
	got, err := f.FetchCredentials(t.Context())
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got != nil {
		t.Errorf("want nil, got %v", got)
	}
}

func TestFetchCredentialsGracefulOnError(t *testing.T) {
	t.Parallel()

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
		_, _ = w.Write([]byte(`{"error":"bad"}`))
	}))
	t.Cleanup(server.Close)

	f := turn.NewFetcherWithClient("tok", "api", server.Client())
	f.SetBaseURL(server.URL)

	_, err := f.FetchCredentials(t.Context())
	if err == nil {
		t.Fatal("expected error for 500 response")
	}
}
