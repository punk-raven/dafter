package config_test

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

func canaryCatalog(percent string) *config.Catalog {
	c := namedCatalog()
	c.Profiles = map[string]json.RawMessage{
		"support": json.RawMessage(`{"version": {"id": "support-v3"}, "agent": {"personaRef": "persona://support/v3"},
			"canary": {"profile": "support-v4", "percent": ` + percent + `}}`),
		"support-v4": json.RawMessage(`{"version": {"id": "support-v4"}, "agent": {"personaRef": "persona://support/v4"}}`),
	}
	return c
}

func resolveArm(t *testing.T, c *config.Catalog, candidate bool) *config.Resolution {
	t.Helper()
	req := request()
	req.Candidate = candidate
	res, err := c.Resolve(req)
	if err != nil {
		t.Fatalf("resolve: %v", err)
	}
	return res
}

func TestTheStableArmRunsTheProfileItNamesAndCarriesItsVersion(t *testing.T) {
	t.Parallel()
	res := resolveArm(t, canaryCatalog("5"), false)
	if res.Config.Agent.PersonaRef != "persona://support/v3" {
		t.Errorf("persona = %q, want the stable one", res.Config.Agent.PersonaRef)
	}
	if res.Config.VersionID() != "support-v3" || res.Config.OnCandidate() {
		t.Errorf("version = %+v, want support-v3 on the stable arm", res.Config.Version)
	}
	if strings.Contains(string(res.Document), "canary") || strings.Contains(string(res.Document), "candidate") {
		t.Errorf("the stable document carries the routing it was resolved by: %s", res.Document)
	}
}

func TestTheCandidateArmSwapsInTheCandidateProfileAndSaysSo(t *testing.T) {
	t.Parallel()
	res := resolveArm(t, canaryCatalog("5"), true)
	if res.Config.Agent.PersonaRef != "persona://support/v4" {
		t.Errorf("persona = %q, want the candidate one", res.Config.Agent.PersonaRef)
	}
	if res.Config.VersionID() != "support-v4" || !res.Config.OnCandidate() {
		t.Errorf("version = %+v, want support-v4 on the candidate arm", res.Config.Version)
	}
	if strings.Contains(string(res.Document), "canary") {
		t.Errorf("the candidate document carries a canary: %s", res.Document)
	}
}

func TestTheArmsHashApart(t *testing.T) {
	t.Parallel()
	c := canaryCatalog("5")
	if resolveArm(t, c, false).Hash == resolveArm(t, c, true).Hash {
		t.Error("both arms resolved to one hash, so a stored session could not say which it ran")
	}
}

func TestACandidateRequestWithoutACanaryResolvesStable(t *testing.T) {
	t.Parallel()
	res := resolveArm(t, catalog(), true)
	if res.Config.OnCandidate() || res.Config.Agent.PersonaRef != "persona://support/v3" {
		t.Errorf("a profile without a canary was routed: %s", res.Document)
	}
}

func TestASessionWithoutAVersionedLayerCarriesNone(t *testing.T) {
	t.Parallel()
	if v := resolve(t, request()).Config.Version; v != nil {
		t.Errorf("version = %+v, want none when no layer states one", v)
	}
}

func TestCanaryForReadsTheProfileTheSessionResolvesWith(t *testing.T) {
	t.Parallel()
	c := canaryCatalog("7")
	route, ok := c.CanaryFor(request())
	if !ok || route.Percent != 7 || route.Profile != "support-v4" || route.CandidateVersion != "support-v4" {
		t.Errorf("route = %+v, %v; want 7%% to support-v4", route, ok)
	}
	byAgent := request()
	byAgent.Profile, byAgent.Agent = "", "asha"
	if _, ok := c.CanaryFor(byAgent); !ok {
		t.Error("an agent that carries the profile did not bring its canary")
	}
	none := request()
	none.Profile = "support-v4"
	if _, ok := c.CanaryFor(none); ok {
		t.Error("a profile without a canary was given one")
	}
}

func TestACanaryAtZeroPercentIsOff(t *testing.T) {
	t.Parallel()
	if route, ok := canaryCatalog("0").CanaryFor(request()); ok {
		t.Errorf("a canary at 0%% routes: %+v", route)
	}
}

func TestASessionOverrideCannotChooseItsVersion(t *testing.T) {
	t.Parallel()
	req := request()
	req.Overrides = json.RawMessage(`{"version": {"id": "support-v4"}, "canary": {"profile": "support-v4", "percent": 100}}`)
	de := resolveErrorIn(t, canaryCatalog("5"), req)
	joined := strings.Join(de.Details, "\n")
	for _, pointer := range []string{"at '/version'", "at '/canary'"} {
		if !strings.Contains(joined, pointer) {
			t.Errorf("no detail %s; the version a session runs is the operator's\n%v", pointer, de)
		}
	}
}

func loadVersioned(t *testing.T, c *config.Catalog) error {
	t.Helper()
	raw, err := json.Marshal(c)
	if err != nil {
		t.Fatal(err)
	}
	_, err = config.LoadCatalog(raw)
	return err
}

func TestACatalogWithAWellFormedCanaryLoads(t *testing.T) {
	t.Parallel()
	if err := loadVersioned(t, canaryCatalog("10")); err != nil {
		t.Fatalf("load: %v", err)
	}
}

func TestTheCatalogRefusesACanaryThatCouldNotRoute(t *testing.T) {
	t.Parallel()
	cases := map[string]struct {
		profiles map[string]string
		pointer  string
	}{
		"unknown candidate": {
			map[string]string{"support": `{"canary": {"profile": "support-v9", "percent": 5}}`},
			"/profiles/support/canary/profile",
		},
		"itself": {
			map[string]string{"support": `{"version": {"id": "a"}, "canary": {"profile": "support", "percent": 5}}`},
			"/profiles/support/canary/profile",
		},
		"unversioned candidate": {
			map[string]string{"support": `{"canary": {"profile": "next", "percent": 5}}`, "next": `{}`},
			"/profiles/support/canary/profile",
		},
		"chained": {
			map[string]string{
				"support": `{"canary": {"profile": "next", "percent": 5}}`,
				"next":    `{"version": {"id": "next"}, "canary": {"profile": "after", "percent": 5}}`,
				"after":   `{"version": {"id": "after"}}`,
			},
			"/profiles/support/canary/profile",
		},
		"over a hundred": {
			map[string]string{"support": `{"canary": {"profile": "next", "percent": 101}}`, "next": `{"version": {"id": "next"}}`},
			"/profiles/support/canary/percent",
		},
		"one id twice": {
			map[string]string{"support": `{"version": {"id": "same"}}`, "zz": `{"version": {"id": "same"}}`},
			"/profiles/zz/version/id",
		},
		"stated arm": {
			map[string]string{"support": `{"version": {"id": "a", "candidate": true}}`},
			"/profiles/support/version/candidate",
		},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			c := catalog()
			c.Profiles = map[string]json.RawMessage{}
			for profile, raw := range tc.profiles {
				c.Profiles[profile] = json.RawMessage(raw)
			}
			err := loadVersioned(t, c)
			var de *errs.Error
			if !errors.As(err, &de) {
				t.Fatalf("want *errs.Error, got %v", err)
			}
			if !strings.Contains(strings.Join(de.Details, "\n"), "at '"+tc.pointer) {
				t.Errorf("no detail points at %s\n%v", tc.pointer, de)
			}
		})
	}
}

func TestAMalformedVersionIsRefusedLikeThePythonHalf(t *testing.T) {
	t.Parallel()
	for name, version := range map[string]string{
		"spaces":        `{"id": "Support V4"}`,
		"empty":         `{"id": ""}`,
		"unknown field": `{"id": "v4", "percent": 5}`,
	} {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			c := catalog()
			c.Profiles = map[string]json.RawMessage{"support": json.RawMessage(`{"version": ` + version + `}`)}
			if de := resolveErrorIn(t, c, request()); de.Code != errs.CodeInvalidConfig {
				t.Errorf("want %s, got %s", errs.CodeInvalidConfig, de.Code)
			}
		})
	}
}
