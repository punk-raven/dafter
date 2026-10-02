package control_test

import (
	"encoding/json"
	"net/http"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
)

type pinnedRelease struct {
	catalog *config.Catalog
	release int64
}

func (p pinnedRelease) Snapshot() config.Snapshot {
	return config.Snapshot{Catalog: p.catalog, Release: p.release}
}

func TestASessionRecordsTheReleaseAndAgentItResolvedFrom(t *testing.T) {
	t.Parallel()
	h := serve(t)
	catalog := embeddedCatalog(t)
	catalog.Agents = map[string]json.RawMessage{
		"asha": json.RawMessage(`{"name": "Asha", "aliases": ["आशा"], "nearMisses": ["Usha"], "profile": "support"}`),
	}
	h.svc.Catalog = pinnedRelease{catalog: catalog, release: 42}

	created := h.create(t, `{"tenantId": "`+tenantID+`", "agent": "asha", "language": "hi", "channel": "webrtc"}`)
	var view struct {
		ReleaseID int64 `json:"releaseId"`
		Agent     struct {
			Name       string `json:"name"`
			PersonaRef string `json:"personaRef"`
		} `json:"agent"`
	}
	if err := json.Unmarshal(created.Config, &view); err != nil {
		t.Fatal(err)
	}
	if view.Agent.Name != "Asha" || view.Agent.PersonaRef != "persona://support/v3" {
		t.Errorf("session resolved agent %q persona %q, want the named agent and its profile's persona", view.Agent.Name, view.Agent.PersonaRef)
	}
	stored, err := h.store.Session(t.Context(), created.SessionID)
	if err != nil {
		t.Fatal(err)
	}
	if stored.ReleaseID != 42 || stored.ConfigHash != created.ConfigHash {
		t.Errorf("stored release %d hash %s, want release 42 beside hash %s", stored.ReleaseID, stored.ConfigHash, created.ConfigHash)
	}

	resp, err := h.server.Client().Get(h.server.URL + "/sessions/" + created.SessionID)
	if err != nil {
		t.Fatal(err)
	}
	defer closeBody(t, resp)
	raw, err := readAll(resp)
	if err != nil {
		t.Fatal(err)
	}
	if err := json.Unmarshal(raw, &view); err != nil || view.ReleaseID != 42 {
		t.Errorf("reading the session back reports release %d (%v)", view.ReleaseID, err)
	}

	if de := h.reject(t, `{"tenantId": "`+tenantID+`", "agent": "nobody", "language": "hi", "channel": "webrtc"}`, http.StatusBadRequest); len(de.Details) == 0 {
		t.Errorf("an unknown agent was rejected without a located problem: %+v", de)
	}
}
