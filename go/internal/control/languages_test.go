package control_test

import (
	"encoding/json"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
)

var focusLanguages = []struct {
	language string
	strategy config.TurnStrategy
	voice    string
}{
	{"hi", config.TurnProviderEndpointing, "priya"},
	{"en-IN", config.TurnSemantic, "priya"},
	{"kn-IN", config.TurnProviderEndpointing, "priya"},
	{"mr-IN", config.TurnProviderEndpointing, "priya"},
	{"te-IN", config.TurnProviderEndpointing, "priya"},
}

func TestEachFocusLanguageResolvesItsOwnRoute(t *testing.T) {
	t.Parallel()
	catalog := embeddedCatalog(t)
	for _, l := range focusLanguages {
		for _, channel := range []config.Channel{config.ChannelWebRTC, config.ChannelTelephony} {
			resolved, err := catalog.Resolve(config.Request{
				SessionID: "s_7f3a9c21", TenantID: tenantID, Language: l.language, Channel: channel,
			})
			if err != nil {
				t.Fatalf("%s on %s: %v", l.language, channel, err)
			}
			p := resolved.Config.Agent.Pipeline
			if p == nil || p.STT == nil || p.LLM == nil || p.TTS == nil {
				t.Fatalf("%s on %s resolved no cascade: %+v", l.language, channel, p)
			}
			route := [3]string{p.STT.Provider + "/" + p.STT.Model, p.LLM.Provider + "/" + p.LLM.Model, p.TTS.Provider + "/" + p.TTS.Model}
			if route != [3]string{"sarvam/saaras:v3-realtime", "sarvam/sarvam-105b", "sarvam/bulbul:v3"} {
				t.Errorf("%s on %s routes %v; Sarvam is the baseline for every focus language", l.language, channel, route)
			}
			if voice := p.TTS.Options["voice"]; voice != l.voice {
				t.Errorf("%s on %s speaks with %v, want %s", l.language, channel, voice, l.voice)
			}
			turn := resolved.Config.Turn
			if turn.Strategy != l.strategy || turn.LocalVADDecidesTurn() != (l.strategy == config.TurnSemantic) {
				t.Errorf("%s on %s ends the turn by %s with a local VAD deciding it %v, want %s",
					l.language, channel, turn.Strategy, turn.LocalVADDecidesTurn(), l.strategy)
			}
			if turn.SilenceMs == 0 || turn.MinSpeechMs == 0 || turn.EndpointingMaxDelayMs == 0 {
				t.Errorf("%s on %s left a turn constant to the provider's default: %+v", l.language, channel, turn)
			}
		}
	}
}

func TestASessionOverrideTurnsOnSwitchingBetweenEveryFocusLanguage(t *testing.T) {
	t.Parallel()
	catalog := embeddedCatalog(t)
	for _, l := range focusLanguages {
		resolved, err := catalog.Resolve(config.Request{
			SessionID: "s_7f3a9c21", TenantID: tenantID, Language: l.language, Channel: config.ChannelWebRTC,
			Overrides: json.RawMessage(`{"agent": {"languageSwitching": {"enabled": true}}}`),
		})
		if err != nil {
			t.Fatalf("%s: %v", l.language, err)
		}
		s := resolved.Config.Agent.LanguageSwitching
		if s == nil || !s.Enabled || len(s.Languages) != len(focusLanguages) {
			t.Errorf("%s: switching resolved to %+v", l.language, s)
		}
	}
	plain, err := catalog.Resolve(config.Request{
		SessionID: "s_7f3a9c21", TenantID: tenantID, Language: "hi", Channel: config.ChannelWebRTC,
	})
	if err != nil {
		t.Fatal(err)
	}
	if plain.Config.Agent.LanguageSwitching.Enabled {
		t.Error("a session switches languages without asking to")
	}
}
