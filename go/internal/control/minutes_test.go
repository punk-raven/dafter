package control_test

import (
	"bytes"
	"encoding/json"
	"net/http"
	"os"
	"testing"
)

const minutesVector = "../../../testdata/events/scribe-minutes.json"

func minutesEvent(t *testing.T, sessionID string, mutate func(doc, payload map[string]any)) string {
	t.Helper()
	raw, err := os.ReadFile(minutesVector)
	if err != nil {
		t.Fatal(err)
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	doc["sessionId"] = sessionID
	if mutate != nil {
		mutate(doc, doc["payload"].(map[string]any))
	}
	out, err := json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	return string(out)
}

func TestTheScribesMinutesAreKeptAndReadBackWithTheWorkerCredential(t *testing.T) {
	t.Parallel()
	h := serve(t)
	got := h.createScribe(t, scribeRequest("open"))
	path := "/sessions/" + got.SessionID + "/minutes"

	if status, raw := h.as(t, workerSecret, http.MethodGet, path, ""); status != http.StatusBadRequest || !bytes.Contains(raw, []byte("/minutes")) {
		t.Errorf("minutes before any were written returned %d %s", status, raw)
	}
	for i, summary := range []string{"पहला", "आखिरी"} {
		body := minutesEvent(t, got.SessionID, func(_, p map[string]any) { p["summary"] = summary })
		status, raw := h.as(t, workerSecret, http.MethodPost, path, body)
		var view struct {
			Version int `json:"version"`
		}
		if status != http.StatusCreated || json.Unmarshal(raw, &view) != nil || view.Version != i+1 {
			t.Fatalf("minutes %d returned %d %s", i+1, status, raw)
		}
	}
	status, raw := h.as(t, workerSecret, http.MethodGet, path, "")
	var read struct {
		Version int `json:"version"`
		Minutes struct {
			Summary string  `json:"summary"`
			CostInr float64 `json:"costInr"`
		} `json:"minutes"`
	}
	if status != http.StatusOK || json.Unmarshal(raw, &read) != nil {
		t.Fatalf("GET minutes returned %d %s", status, raw)
	}
	if read.Version != 2 || read.Minutes.Summary != "आखिरी" || read.Minutes.CostInr != 4.21 {
		t.Errorf("read back %+v, want the latest minutes with their cost", read)
	}

	for name, credential := range map[string]string{"no credential": "", "a client token": got.Token} {
		if status, _ := h.as(t, credential, http.MethodGet, path, ""); status != http.StatusUnauthorized {
			t.Errorf("%s read the minutes: %d", name, status)
		}
		if status, _ := h.as(t, credential, http.MethodPost, path, minutesEvent(t, got.SessionID, nil)); status != http.StatusUnauthorized {
			t.Errorf("%s wrote the minutes: %d", name, status)
		}
	}
}

func TestMinutesMustBeTheFinalOnesOfThisSession(t *testing.T) {
	t.Parallel()
	h := serve(t)
	got := h.createScribe(t, scribeRequest("open"))
	other := h.createScribe(t, scribeRequest("open"))
	without := h.create(t, request("hi", "webrtc"))
	cases := []struct {
		name, session, body, pointer string
	}{
		{"minutes so far", got.SessionID, minutesEvent(t, got.SessionID, func(_, p map[string]any) { p["final"] = false }), "/payload/final"},
		{"not minutes", got.SessionID, minutesEvent(t, got.SessionID, func(d, _ map[string]any) { d["type"] = "recording.started" }), "/type"},
		{"another session", got.SessionID, minutesEvent(t, other.SessionID, nil), "/sessionId"},
		{"no cost", got.SessionID, minutesEvent(t, got.SessionID, func(_, p map[string]any) { delete(p, "costInr") }), "/payload"},
		{"a session without a scribe", without.SessionID, minutesEvent(t, without.SessionID, nil), "/scribe/enabled"},
	}
	for _, c := range cases {
		status, raw := h.as(t, workerSecret, http.MethodPost, "/sessions/"+c.session+"/minutes", c.body)
		if status != http.StatusBadRequest || !bytes.Contains(raw, []byte(c.pointer)) {
			t.Errorf("%s: returned %d %s, want 400 at %s", c.name, status, raw, c.pointer)
		}
	}
	if status, _ := h.as(t, workerSecret, http.MethodGet, "/sessions/"+got.SessionID+"/minutes", ""); status != http.StatusBadRequest {
		t.Errorf("a refused write was stored: GET returned %d", status)
	}
}
