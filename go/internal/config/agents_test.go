package config_test

import (
	"encoding/json"
	"errors"
	"os"
	"regexp"
	"slices"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

const agentVectors = "../../../testdata/agents/definitions.json"

var located = regexp.MustCompile(`^at '([^']*)'`)

func TestAgentDefinitionsMatchTheSharedVectors(t *testing.T) {
	t.Parallel()
	raw, err := os.ReadFile(agentVectors)
	if err != nil {
		t.Fatal(err)
	}
	var f struct {
		Cases []struct {
			Name     string          `json:"name"`
			Document json.RawMessage `json:"document"`
			Rejected *struct {
				Code     errs.ErrorCode `json:"code"`
				Pointers []string       `json:"pointers"`
			} `json:"rejected"`
		} `json:"cases"`
	}
	if err := json.Unmarshal(raw, &f); err != nil {
		t.Fatal(err)
	}
	for _, c := range f.Cases {
		t.Run(c.Name, func(t *testing.T) {
			t.Parallel()
			_, err := config.ParseAgentDefinition(c.Document)
			if c.Rejected == nil {
				if err != nil {
					t.Fatalf("rejected: %v", err)
				}
				return
			}
			var de *errs.Error
			if !errors.As(err, &de) || de.Code != c.Rejected.Code {
				t.Fatalf("want %s, got %v", c.Rejected.Code, err)
			}
			var pointers []string
			for _, d := range de.Details {
				if m := located.FindStringSubmatch(d); m != nil && !slices.Contains(pointers, m[1]) {
					pointers = append(pointers, m[1])
				}
			}
			slices.Sort(pointers)
			if !slices.Equal(pointers, c.Rejected.Pointers) {
				t.Errorf("pointers %q, want %q", pointers, c.Rejected.Pointers)
			}
		})
	}
}

func TestAnAgentDefinitionStatesNothingButItsOwnFields(t *testing.T) {
	t.Parallel()
	_, err := config.ParseAgentDefinition([]byte(`{"name": "Asha", "voice": "priya"}`))
	var de *errs.Error
	if !errors.As(err, &de) || !slices.ContainsFunc(de.Details, func(d string) bool { return located.FindString(d) == "at '/voice'" }) {
		t.Fatalf("an undeclared field was not located: %v", err)
	}
}

func namedCatalog() *config.Catalog {
	c := catalog()
	c.Defaults = json.RawMessage(`{
		"apiVersion": "dafter.dev/v1", "privacyMode": "open",
		"agent": {"enabled": true, "pool": "dafter-py", "name": "Nivya",
			"addressing": {"mode": "transcript", "aliases": ["निव्या"], "nearMisses": ["Navya", "Divya"]}},
		"turn": {"strategy": "auto"},
		"recording": {"enabled": false},
		"budgets": {"turnGapP50Ms": 800, "turnGapP95Ms": 1500}
	}`)
	c.Tenants[tenantID] = json.RawMessage(`{"agent": {"addressing": {"nearMisses": ["Kavya", "Navya"]}}}`)
	c.Agents = map[string]json.RawMessage{
		"asha": json.RawMessage(`{"name": "Asha", "aliases": ["आशा"], "nearMisses": ["Usha"], "profile": "support"}`),
	}
	return c
}

func resolveAgent(t *testing.T, c *config.Catalog, agent, profile, overrides string) *config.ResolvedSessionConfig {
	t.Helper()
	req := request()
	req.Agent, req.Profile = agent, profile
	if overrides != "" {
		req.Overrides = json.RawMessage(overrides)
	}
	res, err := c.Resolve(req)
	if err != nil {
		t.Fatalf("resolve: %v", err)
	}
	return res.Config
}

func TestNearMissesAddUpAcrossTheLayers(t *testing.T) {
	t.Parallel()
	cfg := resolveAgent(t, namedCatalog(), "", "", `{"agent": {"addressing": {"nearMisses": ["Bhavya"]}}}`)
	want := []string{"Navya", "Divya", "Kavya", "Bhavya"}
	if got := cfg.Agent.Addressing.NearMisses; !slices.Equal(got, want) {
		t.Errorf("near misses %q, want the union %q", got, want)
	}
	if got := cfg.Agent.Addressing.Aliases; !slices.Equal(got, []string{"निव्या"}) {
		t.Errorf("aliases %q: a list other than near misses is not merged", got)
	}
}

func TestASessionThatNamesNoAgentResolvesAsTheDefaultsState(t *testing.T) {
	t.Parallel()
	cfg := resolveAgent(t, namedCatalog(), "", "", "")
	if cfg.Agent.Name != "Nivya" || cfg.Agent.PersonaRef != "" {
		t.Errorf("agent %q persona %q, want the defaults' Nivya and no persona", cfg.Agent.Name, cfg.Agent.PersonaRef)
	}
}

func TestANamedAgentBringsItsNameAliasesNearMissesAndPersona(t *testing.T) {
	t.Parallel()
	cfg := resolveAgent(t, namedCatalog(), "asha", "", "")
	if cfg.Agent.Name != "Asha" || !slices.Equal(cfg.Agent.Addressing.Aliases, []string{"आशा"}) {
		t.Errorf("agent %q aliases %q, want Asha and only her own spellings", cfg.Agent.Name, cfg.Agent.Addressing.Aliases)
	}
	if want := []string{"Navya", "Divya", "Usha", "Kavya"}; !slices.Equal(cfg.Agent.Addressing.NearMisses, want) {
		t.Errorf("near misses %q, want %q", cfg.Agent.Addressing.NearMisses, want)
	}
	if cfg.Agent.PersonaRef != "persona://support/v3" {
		t.Errorf("persona %q, want the one the agent's profile states", cfg.Agent.PersonaRef)
	}
}

func TestANamedAgentWithoutAliasesKeepsNoneOfTheDefaultSpellings(t *testing.T) {
	t.Parallel()
	c := namedCatalog()
	c.Agents["meera"] = json.RawMessage(`{"name": "Meera"}`)
	if got := resolveAgent(t, c, "meera", "", "").Agent.Addressing.Aliases; len(got) != 0 {
		t.Errorf("aliases %q: another agent's spellings would wake this one", got)
	}
}

func TestAnUnknownAgentOrItsMissingProfileIsLocated(t *testing.T) {
	t.Parallel()
	c := namedCatalog()
	req := request()
	req.Agent, req.Profile = "nobody", ""
	if de := resolveErrorIn(t, c, req); !slices.ContainsFunc(de.Details, func(d string) bool { return located.FindString(d) == "at '/agent'" }) {
		t.Errorf("details %q do not locate the unknown agent", de.Details)
	}
	c.Agents["asha"] = json.RawMessage(`{"name": "Asha", "profile": "sales"}`)
	req.Agent = "asha"
	if de := resolveErrorIn(t, c, req); !slices.ContainsFunc(de.Details, func(d string) bool { return located.FindString(d) == "at '/agents/asha/profile'" }) {
		t.Errorf("details %q do not locate the agent's missing profile", de.Details)
	}
}

func TestADocumentSetAssemblesBackIntoTheCatalog(t *testing.T) {
	t.Parallel()
	c := namedCatalog()
	docs, err := c.Documents()
	if err != nil {
		t.Fatal(err)
	}
	assembled, err := config.Assemble(docs)
	if err != nil {
		t.Fatalf("assemble: %v", err)
	}
	for _, agent := range []string{"", "asha"} {
		req := request()
		req.Agent = agent
		want, err := c.Resolve(req)
		if err != nil {
			t.Fatal(err)
		}
		got, err := assembled.Resolve(req)
		if err != nil {
			t.Fatal(err)
		}
		if got.Hash != want.Hash {
			t.Errorf("agent %q: assembled hash %s, want %s", agent, got.Hash, want.Hash)
		}
	}
	delete(docs, config.KindDefaults)
	if _, err := config.Assemble(docs); err == nil {
		t.Error("a document set without defaults assembled")
	}
}
