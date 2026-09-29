package config_test

import (
	"encoding/json"
	"reflect"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

const groqRoute = "groq/qwen/qwen3.8-27b"

func routedCatalog() *config.Catalog {
	c := catalog()
	c.Languages["hi"] = config.Axis{
		Tuning: c.Languages["hi"].Tuning,
		Overlay: json.RawMessage(`{"agent": {"pipeline": {
			"stt": {"provider": "sarvam", "model": "saaras"},
			"llm": {"provider": "sarvam", "model": "sarvam-105b", "region": "ap-south-1",
				"credentialRef": "secret://tenants/t_9c21a4be/sarvam/api-key",
				"options": {"prewarm": true, "thinking": false}}
		}}}`),
	}
	c.LLMs = map[string]json.RawMessage{
		groqRoute: json.RawMessage(`{"provider": "groq", "model": "qwen/qwen3.8-27b",
			"credentialRef": "secret://tenants/t_9c21a4be/groq/api-key",
			"options": {"reasoningEffort": "none"}}`),
	}
	return c
}

func routed(llm, overrides string) config.Request {
	req := request()
	req.Language, req.LLM = "hi", llm
	req.Overrides = json.RawMessage(overrides)
	return req
}

func TestResolveLandsAChosenLLMRouteWholeOverTheLanguageOverlay(t *testing.T) {
	t.Parallel()
	res, err := routedCatalog().Resolve(routed(groqRoute, ""))
	if err != nil {
		t.Fatal(err)
	}
	want := &config.ProviderRef{
		Provider:      "groq",
		Model:         "qwen/qwen3.8-27b",
		CredentialRef: "secret://tenants/t_9c21a4be/groq/api-key",
		Options:       map[string]any{"reasoningEffort": "none"},
	}
	c := res.Config
	if !reflect.DeepEqual(c.Agent.Pipeline.LLM, want) {
		t.Errorf("the route merged into the language's LLM instead of replacing it: %+v", c.Agent.Pipeline.LLM)
	}
	if c.LLM != groqRoute {
		t.Errorf("the document names route %q, want %q", c.LLM, groqRoute)
	}
	if c.Agent.Pipeline.STT == nil || c.Agent.Pipeline.STT.Provider != "sarvam" {
		t.Errorf("the route touched another stage: %+v", c.Agent.Pipeline.STT)
	}
}

func TestResolveWithoutARouteRunsTheLanguagesLLMAndNamesNone(t *testing.T) {
	t.Parallel()
	res, err := routedCatalog().Resolve(routed("", ""))
	if err != nil {
		t.Fatal(err)
	}
	if res.Config.LLM != "" || strings.Contains(string(res.Document), `"llm":"`) {
		t.Errorf("a session that chose no route names one: %s", res.Document)
	}
	if llm := res.Config.Agent.Pipeline.LLM; llm == nil || llm.Provider != "sarvam" {
		t.Errorf("the language's LLM was lost: %+v", llm)
	}
}

func TestResolveRefusesAnLLMRouteTheCatalogDoesNotCarry(t *testing.T) {
	t.Parallel()
	de := resolveErrorIn(t, routedCatalog(), routed("groq/llama-9", ""))
	if de.Code != errs.CodeUnsupportedCapability {
		t.Errorf("want %s, got %s", errs.CodeUnsupportedCapability, de.Code)
	}
	if !strings.Contains(strings.Join(de.Details, "\n"), "'/llm': no LLM route of that name is registered") {
		t.Errorf("the refusal does not point at /llm: %v", de)
	}
}

func TestResolveRefusesAnOverrideThatNamesOrTunesTheRoute(t *testing.T) {
	t.Parallel()
	cases := map[string]struct {
		llm, overrides, detail string
	}{
		"names a route": {
			"", `{"llm": "` + groqRoute + `"}`,
			"'/llm': is chosen by the session request's llm field",
		},
		"tunes the chosen route": {
			groqRoute, `{"agent": {"pipeline": {"llm": {"options": {"temperature": 0.9}}}}}`,
			"'/agent/pipeline/llm/options/temperature': the " + groqRoute + " LLM route pins this",
		},
		"points the route at another host": {
			groqRoute, `{"agent": {"pipeline": {"llm": {"credentialRef": "secret://tenants/t_9c21a4be/dafter/worker-secret"}}}}`,
			"'/agent/pipeline/llm/credentialRef': the " + groqRoute + " LLM route pins this",
		},
	}
	for name, tc := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			de := resolveErrorIn(t, routedCatalog(), routed(tc.llm, tc.overrides))
			if de.Code != errs.CodeInvalidConfig {
				t.Errorf("want %s, got %s", errs.CodeInvalidConfig, de.Code)
			}
			if !strings.Contains(strings.Join(de.Details, "\n"), tc.detail) {
				t.Errorf("no detail says %q: %v", tc.detail, de)
			}
		})
	}
}

func TestLoadCatalogRefusesAnLLMRouteNoSessionCouldRun(t *testing.T) {
	t.Parallel()
	cases := map[string]string{
		"named apart from its ref": `{"groq/fast": {"provider": "groq", "model": "qwen/qwen3.8-27b"}}`,
		"not a provider ref":       `{"groq/m": {"provider": "groq", "model": "m", "baseUrl": "https://example.com"}}`,
		"a bad provider name":      `{"Groq/m": {"provider": "Groq", "model": "m"}}`,
	}
	for name, routes := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			raw := `{"defaults": {}, "llms": ` + routes + `}`
			if _, err := config.LoadCatalog([]byte(raw)); err == nil {
				t.Error("the catalog loaded")
			}
		})
	}
	if _, err := config.LoadCatalog([]byte(`{"defaults": {}, "llms": {"groq/openai/gpt-oss-20b": {"provider": "groq", "model": "openai/gpt-oss-20b"}}}`)); err != nil {
		t.Errorf("a well-named route was refused: %v", err)
	}
}
