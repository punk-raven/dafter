package control_test

import (
	"encoding/json"
	"maps"
	"net/http"
	"reflect"
	"slices"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
)

func TestEveryLLMRouteResolvesForEveryFocusLanguage(t *testing.T) {
	t.Parallel()
	catalog := embeddedCatalog(t)
	routes := slices.Sorted(maps.Keys(catalog.LLMs))
	if len(routes) < 2 || !slices.Contains(routes, "sarvam/sarvam-105b") {
		t.Fatalf("the catalog offers routes %v; the test client's default is sarvam/sarvam-105b", routes)
	}
	for _, l := range focusLanguages {
		base, err := catalog.Resolve(config.Request{
			SessionID: "s_7f3a9c21", TenantID: tenantID, Language: l.language, Channel: config.ChannelWebRTC,
		})
		if err != nil {
			t.Fatalf("%s: %v", l.language, err)
		}
		for _, route := range routes {
			resolved, err := catalog.Resolve(config.Request{
				SessionID: "s_7f3a9c21", TenantID: tenantID, Language: l.language, Channel: config.ChannelWebRTC, LLM: route,
			})
			if err != nil {
				t.Fatalf("%s with %s: %v", l.language, route, err)
			}
			llm := resolved.Config.Agent.Pipeline.LLM
			if resolved.Config.LLM != route || llm.Provider+"/"+llm.Model != route {
				t.Errorf("%s with %s resolved route %q running %s/%s", l.language, route, resolved.Config.LLM, llm.Provider, llm.Model)
			}
			if !reflect.DeepEqual(resolved.Config.Agent.Pipeline.STT, base.Config.Agent.Pipeline.STT) ||
				!reflect.DeepEqual(resolved.Config.Agent.Pipeline.TTS, base.Config.Agent.Pipeline.TTS) {
				t.Errorf("%s with %s changed a stage other than the LLM", l.language, route)
			}
		}
		sarvam, _ := catalog.Resolve(config.Request{
			SessionID: "s_7f3a9c21", TenantID: tenantID, Language: l.language, Channel: config.ChannelWebRTC, LLM: "sarvam/sarvam-105b",
		})
		if !reflect.DeepEqual(sarvam.Config.Agent.Pipeline.LLM, base.Config.Agent.Pipeline.LLM) {
			t.Errorf("%s: choosing sarvam/sarvam-105b runs %+v, not the language's own %+v",
				l.language, sarvam.Config.Agent.Pipeline.LLM, base.Config.Agent.Pipeline.LLM)
		}
	}
}

func TestASessionRequestChoosesItsLLMRouteByName(t *testing.T) {
	t.Parallel()
	h := serve(t)
	got := h.create(t, `{"tenantId":"`+tenantID+`","language":"hi","channel":"webrtc","llm":"groq/qwen/qwen3.8-27b"}`)
	var doc struct {
		LLM   string `json:"llm"`
		Agent struct {
			Pipeline struct {
				LLM config.ProviderRef `json:"llm"`
			} `json:"pipeline"`
		} `json:"agent"`
	}
	if err := json.Unmarshal(got.Config, &doc); err != nil {
		t.Fatal(err)
	}
	if doc.LLM != "groq/qwen/qwen3.8-27b" || doc.Agent.Pipeline.LLM.Provider != "groq" || doc.Agent.Pipeline.LLM.Model != "qwen/qwen3.8-27b" {
		t.Errorf("the stored document runs %+v under route %q", doc.Agent.Pipeline.LLM, doc.LLM)
	}
	if strings.Contains(string(got.Config), "API_KEY") || strings.Contains(string(got.Config), "https://") {
		t.Errorf("the document carries a key or an endpoint: %s", got.Config)
	}

	de := h.reject(t, `{"tenantId":"`+tenantID+`","language":"hi","channel":"webrtc","llm":"groq/unlisted"}`, http.StatusBadRequest)
	if !strings.Contains(strings.Join(de.Details, "\n"), "'/llm'") {
		t.Errorf("an unknown route was not located at /llm: %+v", de)
	}
}

func TestTheTestClientSpeechTogglesResolveForEveryFocusLanguage(t *testing.T) {
	t.Parallel()
	catalog := embeddedCatalog(t)
	off := json.RawMessage(`{
		"agent": {"speech": {"fillers": {"enabled": false}, "normalization": "provider"}},
		"turn": {"interruption": {"backchannel": {"enabled": false}}}
	}`)
	for _, l := range focusLanguages {
		resolved, err := catalog.Resolve(config.Request{
			SessionID: "s_7f3a9c21", TenantID: tenantID, Language: l.language, Channel: config.ChannelWebRTC,
			LLM: "groq/qwen/qwen3.8-27b", Overrides: off,
		})
		if err != nil {
			t.Fatalf("%s: an overlay pins a setting the test client toggles: %v", l.language, err)
		}
		var doc struct {
			Agent struct {
				Speech struct {
					Normalization string `json:"normalization"`
					Fillers       struct {
						Enabled bool `json:"enabled"`
					} `json:"fillers"`
				} `json:"speech"`
			} `json:"agent"`
			Turn struct {
				Interruption struct {
					Backchannel struct {
						Enabled bool `json:"enabled"`
					} `json:"backchannel"`
				} `json:"interruption"`
			} `json:"turn"`
		}
		if err := json.Unmarshal(resolved.Document, &doc); err != nil {
			t.Fatal(err)
		}
		if doc.Agent.Speech.Fillers.Enabled || doc.Turn.Interruption.Backchannel.Enabled || doc.Agent.Speech.Normalization != "provider" {
			t.Errorf("%s resolved the toggles to %+v", l.language, doc)
		}
	}
}
