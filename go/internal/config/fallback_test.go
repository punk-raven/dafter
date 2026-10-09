package config_test

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

var groqFallback = map[string]any{
	"provider": "groq", "model": "qwen/qwen3.8-27b", "credentialRef": "secret://tenants/t_9c21a4be/groq/api-key",
}

func fallbackDocument(t *testing.T, fallback map[string]any) []byte {
	t.Helper()
	raw, err := json.Marshal(validConfig(t))
	if err != nil {
		t.Fatal(err)
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	doc["agent"].(map[string]any)["pipeline"] = map[string]any{"fallback": fallback}
	raw, err = json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestAPipelineNamesItsLLMFallbackAndAnEmptyVoiceSlot(t *testing.T) {
	t.Parallel()
	c, err := config.Parse(fallbackDocument(t, map[string]any{"llm": []any{groqFallback}, "tts": []any{}}))
	if err != nil {
		t.Fatalf("an LLM fallback with an empty voice slot rejected: %v", err)
	}
	f := c.Agent.Pipeline.Fallback
	if f == nil || len(f.LLM) != 1 || f.LLM[0].Provider != "groq" || len(f.TTS) != 0 {
		t.Errorf("decoded %+v", f)
	}
}

func TestAFallbackTheSchemaDoesNotAllowIsRefused(t *testing.T) {
	t.Parallel()
	for name, fallback := range map[string]map[string]any{
		"stt has no fallback": {"stt": []any{groqFallback}},
		"three llm fallbacks": {"llm": []any{groqFallback, groqFallback, groqFallback}},
	} {
		_, err := config.Parse(fallbackDocument(t, fallback))
		var de *errs.Error
		if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
			t.Fatalf("%s: want %s, got %v", name, errs.CodeInvalidConfig, err)
		}
		if !strings.Contains(strings.Join(de.Details, "\n"), "/agent/pipeline/fallback") {
			t.Errorf("%s: no detail points at the fallback: %v", name, de)
		}
	}
}
