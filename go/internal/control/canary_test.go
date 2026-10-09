package control_test

import (
	"encoding/json"
	"fmt"
	"maps"
	"net/http"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
)

func serveWithCanary(t *testing.T, percent int) *harness {
	t.Helper()
	catalog := *embeddedCatalog(t)
	catalog.Profiles = maps.Clone(catalog.Profiles)
	catalog.Profiles["support"] = json.RawMessage(fmt.Sprintf(
		`{"version": {"id": "support-v3"}, "agent": {"personaRef": "persona://support/v3"},
		"canary": {"profile": "support-v4", "percent": %d}}`, percent))
	catalog.Profiles["support-v4"] = json.RawMessage(`{"version": {"id": "support-v4"}, "agent": {"personaRef": "persona://support/v4"}}`)
	return serveCatalog(t, &catalog)
}

func deviceRequest(device string) string {
	return `{"tenantId":"` + tenantID + `","profile":"support","language":"hi","channel":"webrtc","device":"` + device + `"}`
}

func sessionVersion(t *testing.T, r sessionResponse) config.Version {
	t.Helper()
	cfg, err := config.Parse(r.Config)
	if err != nil {
		t.Fatalf("parse returned config: %v", err)
	}
	if cfg.Version == nil {
		t.Fatalf("the session carries no version: %s", r.Config)
	}
	return *cfg.Version
}

func device(i int) string {
	return fmt.Sprintf("canary-device-%08d-key", i)
}

func TestACanaryAtAHundredPercentSendsEverySessionToTheCandidate(t *testing.T) {
	t.Parallel()
	h := serveWithCanary(t, 100)
	got := sessionVersion(t, h.create(t, request("hi", "webrtc")))
	if got.ID != "support-v4" || !got.Candidate {
		t.Errorf("version = %+v, want the candidate support-v4", got)
	}
}

func TestACanaryAtZeroPercentLeavesEverySessionStable(t *testing.T) {
	t.Parallel()
	h := serveWithCanary(t, 0)
	for i := range 20 {
		if got := sessionVersion(t, h.create(t, deviceRequest(device(i)))); got.ID != "support-v3" || got.Candidate {
			t.Fatalf("version = %+v with the canary off", got)
		}
	}
}

func TestOneDeviceStaysOnOneArm(t *testing.T) {
	t.Parallel()
	h := serveWithCanary(t, 50)
	arms := map[bool]int{}
	for i := range 16 {
		first := sessionVersion(t, h.create(t, deviceRequest(device(i))))
		for range 3 {
			if again := sessionVersion(t, h.create(t, deviceRequest(device(i)))); again != first {
				t.Fatalf("device %d moved from %+v to %+v", i, first, again)
			}
		}
		arms[first.Candidate]++
	}
	if arms[true] == 0 || arms[false] == 0 {
		t.Errorf("16 devices at 50%% all landed on one arm: %v", arms)
	}
}

func TestTheStoredSessionCarriesTheArmItRan(t *testing.T) {
	t.Parallel()
	h := serveWithCanary(t, 100)
	got := h.create(t, request("hi", "webrtc"))
	stored, err := h.store.Session(t.Context(), got.SessionID)
	if err != nil {
		t.Fatal(err)
	}
	cfg, err := config.Parse(stored.Config)
	if err != nil {
		t.Fatal(err)
	}
	if !cfg.OnCandidate() || cfg.Agent.PersonaRef != "persona://support/v4" {
		t.Errorf("stored session ran %+v with %s", cfg.Version, cfg.Agent.PersonaRef)
	}
}

func TestASessionCannotPickItsOwnArm(t *testing.T) {
	t.Parallel()
	body := `{"tenantId":"` + tenantID + `","profile":"support","language":"hi","channel":"webrtc",
		"overrides":{"version":{"id":"support-v4","candidate":true}}}`
	de := serveWithCanary(t, 5).reject(t, body, http.StatusBadRequest)
	if !strings.Contains(strings.Join(de.Details, "\n"), "at '/version'") {
		t.Errorf("no detail points at /version: %v", de.Details)
	}
}
