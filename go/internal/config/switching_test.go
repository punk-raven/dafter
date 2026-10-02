package config_test

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

func switchingDocument(t *testing.T, switching map[string]any) []byte {
	t.Helper()
	raw, err := json.Marshal(validConfig(t))
	if err != nil {
		t.Fatal(err)
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	doc["agent"].(map[string]any)["languageSwitching"] = switching
	raw, err = json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestASessionMaySwitchBetweenLanguagesThatIncludeItsOwn(t *testing.T) {
	t.Parallel()
	c, err := config.Parse(switchingDocument(t, map[string]any{
		"enabled": true, "languages": []string{"en-IN", "hi", "kn-IN"}, "minConfidence": 0.7, "minWords": 2,
	}))
	if err != nil {
		t.Fatalf("switching between the session's language and two others rejected: %v", err)
	}
	s := c.Agent.LanguageSwitching
	if !s.Enabled || len(s.Languages) != 3 || s.MinConfidence == nil || *s.MinConfidence != 0.7 || s.MinWords != 2 {
		t.Errorf("decoded %+v", s)
	}
	if _, err := config.Parse(switchingDocument(t, map[string]any{"enabled": false, "languages": []string{"hi"}})); err != nil {
		t.Errorf("a disabled switch is not checked against the session's language: %v", err)
	}
}

func TestSwitchingThatLeavesOutTheSessionLanguageIsRefused(t *testing.T) {
	t.Parallel()
	for name, switching := range map[string]map[string]any{
		"other languages only": {"enabled": true, "languages": []string{"hi", "kn-IN"}},
		"no languages":         {"enabled": true},
	} {
		_, err := config.Parse(switchingDocument(t, switching))
		var de *errs.Error
		if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
			t.Fatalf("%s: want %s, got %v", name, errs.CodeInvalidConfig, err)
		}
		if !strings.Contains(strings.Join(de.Details, "\n"), "/agent/languageSwitching/languages") {
			t.Errorf("%s: no detail points at the languages: %v", name, de)
		}
	}
}

func TestSwitchingBoundsAreStatedInTheSchema(t *testing.T) {
	t.Parallel()
	for pointer, switching := range map[string]map[string]any{
		"/agent/languageSwitching/minConfidence": {"enabled": true, "languages": []string{"en-IN"}, "minConfidence": 1.5},
		"/agent/languageSwitching/minWords":      {"enabled": true, "languages": []string{"en-IN"}, "minWords": 0},
		"/agent/languageSwitching/languages/0":   {"enabled": true, "languages": []string{"English"}},
		"/agent/languageSwitching":               {"enabled": true, "languages": []string{"en-IN"}, "detect": "always"},
	} {
		_, err := config.Parse(switchingDocument(t, switching))
		var de *errs.Error
		if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
			t.Fatalf("%s: want %s, got %v", pointer, errs.CodeInvalidConfig, err)
		}
		if !strings.Contains(strings.Join(de.Details, "\n"), "'"+pointer) {
			t.Errorf("no detail points at %s: %v", pointer, de)
		}
	}
}
