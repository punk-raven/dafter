package configcheck_test

import (
	"encoding/json"
	"errors"
	"os"
	"regexp"
	"slices"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/configcheck"
	"github.com/punk-raven/dafter/go/internal/errs"
)

const tenantID = "t_9c21a4be"

var located = regexp.MustCompile(`^at '([^']*)'`)

func shipped(t *testing.T) config.Documents {
	t.Helper()
	raw, err := os.ReadFile("../../cmd/dafter-control/catalog.json")
	if err != nil {
		t.Fatal(err)
	}
	catalog, err := config.LoadCatalog(raw)
	if err != nil {
		t.Fatal(err)
	}
	docs, err := catalog.Documents()
	if err != nil {
		t.Fatal(err)
	}
	return docs
}

func rejected(t *testing.T, err error) []string {
	t.Helper()
	var de *errs.Error
	if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("want an invalid_config error, got %v", err)
	}
	var pointers []string
	for _, d := range de.Details {
		m := located.FindStringSubmatch(d)
		if m == nil {
			t.Fatalf("a problem is not located by a pointer: %q", d)
		}
		pointers = append(pointers, m[1])
	}
	return pointers
}

func TestTheShippedCatalogPassesTheWholeCheck(t *testing.T) {
	t.Parallel()
	if _, err := configcheck.All(shipped(t)); err != nil {
		t.Fatalf("the embedded catalog does not pass its own check: %v", err)
	}
}

func TestAValidAgentIsAccepted(t *testing.T) {
	t.Parallel()
	docs := shipped(t)
	next, err := configcheck.Write(docs, configcheck.Change{
		Kind: config.KindAgents, Name: "asha",
		Document: json.RawMessage(`{"name": "Asha", "aliases": ["आशा"], "nearMisses": ["Usha"], "profile": "support"}`),
	})
	if err != nil {
		t.Fatalf("rejected: %v", err)
	}
	if _, ok := next[config.KindAgents]["asha"]; !ok {
		t.Error("the accepted agent is not in the next draft")
	}
	if _, ok := docs[config.KindAgents]["asha"]; ok {
		t.Error("checking a change edited the draft it was handed")
	}
}

func TestAWriteIsRejectedWithEveryProblemLocated(t *testing.T) {
	t.Parallel()
	cases := map[string]struct {
		change configcheck.Change
		want   []string
	}{
		"a document the agent schema refuses": {
			configcheck.Change{Kind: config.KindAgents, Name: "asha", Document: json.RawMessage(`{"name": "", "profile": "Support Team"}`)},
			[]string{"/agents/asha/name", "/agents/asha/profile"},
		},
		"an agent named one of the near misses below it": {
			configcheck.Change{Kind: config.KindAgents, Name: "navya", Document: json.RawMessage(`{"name": "Navya"}`)},
			[]string{"/agent/addressing/nearMisses"},
		},
		"an agent whose persona profile is not stored": {
			configcheck.Change{Kind: config.KindAgents, Name: "asha", Document: json.RawMessage(`{"name": "Asha", "profile": "sales"}`)},
			[]string{"/agents/asha/profile"},
		},
		"a tenant whose settings break the resolved schema and carry a key": {
			configcheck.Change{Kind: config.KindTenants, Name: "t_12345678", Document: json.RawMessage(
				`{"budgets": {"maxSessionCostUsd": -1}, "agent": {"pipeline": {"llm": {"provider": "groq", "options": {"apiKey": "gsk_live_0123456789abcdef"}}}}}`)},
			[]string{"/budgets/maxSessionCostUsd", "/tenants/t_12345678/agent/pipeline/llm/options/apiKey"},
		},
		"a tenant named by anything but its opaque id": {
			configcheck.Change{Kind: config.KindTenants, Name: "acme", Document: json.RawMessage(`{}`)},
			[]string{"/tenants/acme"},
		},
		"a channel overlay no session could select": {
			configcheck.Change{Kind: config.KindChannels, Name: "carrier_pigeon", Document: json.RawMessage(`{}`)},
			[]string{"/channels/carrier_pigeon"},
		},
		"a language overlay with a field beside tuning and overlay": {
			configcheck.Change{Kind: config.KindLanguages, Name: "ta-IN", Document: json.RawMessage(`{"turn": {}}`)},
			[]string{"/languages/ta-IN"},
		},
		"a profile that is not an object": {
			configcheck.Change{Kind: config.KindProfiles, Name: "sales", Document: json.RawMessage(`["agent"]`)},
			[]string{"/profiles/sales"},
		},
		"deleting the profile an agent links": {
			configcheck.Change{Kind: config.KindProfiles, Name: "support"},
			[]string{"/agents/asha/profile"},
		},
		"deleting what is not stored": {
			configcheck.Change{Kind: config.KindProfiles, Name: "sales"},
			nil,
		},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			docs := shipped(t)
			docs.Set(config.KindAgents, "asha", json.RawMessage(`{"name": "Asha", "profile": "support"}`))
			_, err := configcheck.Write(docs, tc.change)
			if err == nil {
				t.Fatal("the change was accepted")
			}
			got := rejected(t, err)
			for _, want := range tc.want {
				if !slices.Contains(got, want) {
					t.Errorf("pointers %q do not include %q", got, want)
				}
			}
		})
	}
}

func TestADryRunNamesTheSessionItResolved(t *testing.T) {
	t.Parallel()
	_, err := configcheck.Write(shipped(t), configcheck.Change{
		Kind: config.KindAgents, Name: "navya", Document: json.RawMessage(`{"name": "Navya"}`),
	})
	var de *errs.Error
	if !errors.As(err, &de) || len(de.Details) != 1 {
		t.Fatalf("want one deduplicated problem, got %v", err)
	}
	for _, part := range []string{"tenant " + tenantID, "agent navya", "other combination(s)"} {
		if !strings.Contains(de.Details[0], part) {
			t.Errorf("problem %q does not say %q", de.Details[0], part)
		}
	}
}

func TestPastedSecretsAreScreenedOut(t *testing.T) {
	t.Parallel()
	doc := `{
		"agent": {"pipeline": {
			"stt": {"provider": "sarvam", "credentialRef": "secret://tenants/t_9c21a4be/sarvam/api-key",
				"options": {"maxTokens": 200, "tokenTtlSeconds": 60, "apiKey": "secret://tenants/t_9c21a4be/sarvam/api-key"}},
			"llm": {"provider": "groq", "options": {
				"api_key": "abc123", "clientSecret": "s3cr3t", "headers": {"Authorization": "Bearer abcdefghijklmnop"},
				"auth": {"user": "u"}, "extraBody": {"note": "sk-proj-0123456789abcdefghij"}}},
			"tts": {"provider": "sarvam", "credentialRef": "AIzaSyD0123456789abcdefghij"}
		}}
	}`
	got := configcheck.ScreenSecrets(json.RawMessage(doc))
	var pointers []string
	for _, p := range got {
		pointers = append(pointers, located.FindStringSubmatch(p)[1])
	}
	want := []string{
		"/agent/pipeline/llm/options/api_key",
		"/agent/pipeline/llm/options/auth",
		"/agent/pipeline/llm/options/clientSecret",
		"/agent/pipeline/llm/options/extraBody/note",
		"/agent/pipeline/llm/options/headers/Authorization",
		"/agent/pipeline/tts/credentialRef",
	}
	if !slices.Equal(pointers, want) {
		t.Errorf("screened %q, want %q", pointers, want)
	}
	for _, p := range got {
		if strings.Contains(p, "abc123") || strings.Contains(p, "s3cr3t") || strings.Contains(p, "sk-proj") {
			t.Errorf("a problem repeats the secret it found: %q", p)
		}
	}
}

func TestTheShippedCatalogCarriesNoPastedSecret(t *testing.T) {
	t.Parallel()
	docs := shipped(t)
	for kind, byName := range docs {
		for name, doc := range byName {
			if problems := configcheck.ScreenSecrets(doc); len(problems) > 0 {
				t.Errorf("%s/%s: %q", kind, name, problems)
			}
		}
	}
}
