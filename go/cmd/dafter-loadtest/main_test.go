package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

// runLoad must return once the duration has passed. It used to hand every
// goroutine the same time.After channel, which only one of them could
// receive from, so the reporter never exited and the run hung forever.
func TestRunLoadReturnsAfterDuration(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasSuffix(r.URL.Path, "/join") {
			w.WriteHeader(http.StatusCreated)
			return
		}
		w.WriteHeader(http.StatusCreated)
		_ = json.NewEncoder(w).Encode(createResponse{SessionID: "s_00000000"})
	}))
	defer srv.Close()

	cfg := &loadConfig{
		target:          srv.URL,
		users:           4,
		duration:        300 * time.Millisecond,
		ramp:            50 * time.Millisecond,
		joinsPerSession: 2,
	}

	done := make(chan *stats, 1)
	go func() { done <- runLoad(context.Background(), cfg) }()

	select {
	case st := <-done:
		if st.success == 0 {
			t.Fatal("no request succeeded")
		}
		if st.errors != 0 {
			t.Fatalf("%d requests failed", st.errors)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("runLoad did not return after its duration")
	}
}
