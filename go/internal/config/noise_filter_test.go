package config_test

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

func noiseFilterDocument(t *testing.T, filter any) []byte {
	t.Helper()
	raw, err := json.Marshal(validConfig(t))
	if err != nil {
		t.Fatal(err)
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	doc["agent"].(map[string]any)["pipeline"] = map[string]any{"noiseFilter": filter}
	raw, err = json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestAPipelineNamesEachNoiseFilterTheSchemaAllows(t *testing.T) {
	t.Parallel()
	for _, filter := range []string{"off", "nc", "bvc", "bvc_telephony"} {
		c, err := config.Parse(noiseFilterDocument(t, filter))
		if err != nil {
			t.Fatalf("%s rejected: %v", filter, err)
		}
		if got := c.Agent.Pipeline.NoiseFilter; got != filter {
			t.Errorf("%s decoded as %q", filter, got)
		}
	}
}

func TestANoiseFilterTheSchemaDoesNotAllowIsRefused(t *testing.T) {
	t.Parallel()
	for _, filter := range []any{"krisp", "rnnoise", true} {
		_, err := config.Parse(noiseFilterDocument(t, filter))
		var de *errs.Error
		if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
			t.Fatalf("%v: want %s, got %v", filter, errs.CodeInvalidConfig, err)
		}
		if !strings.Contains(strings.Join(de.Details, "\n"), "/agent/pipeline/noiseFilter") {
			t.Errorf("%v: no detail points at the noise filter: %v", filter, de)
		}
	}
}

func TestASessionOverrideChoosesTheNoiseFilter(t *testing.T) {
	t.Parallel()
	req := request()
	req.Overrides = json.RawMessage(`{"agent": {"pipeline": {"noiseFilter": "bvc_telephony"}}}`)
	if got := resolve(t, req).Config.Agent.Pipeline.NoiseFilter; got != "bvc_telephony" {
		t.Errorf("the override's noise filter was not applied: %q", got)
	}
}

func TestTheCatalogTurnsTheNoiseFilterOffEverywhere(t *testing.T) {
	t.Parallel()
	raw, err := os.ReadFile(filepath.Join("..", "..", "cmd", "dafter-control", "catalog.json"))
	if err != nil {
		t.Fatal(err)
	}
	var catalog struct {
		Defaults struct {
			Agent struct {
				Pipeline struct {
					NoiseFilter string `json:"noiseFilter"`
				} `json:"pipeline"`
			} `json:"agent"`
		} `json:"defaults"`
	}
	if err := json.Unmarshal(raw, &catalog); err != nil {
		t.Fatal(err)
	}
	if got := catalog.Defaults.Agent.Pipeline.NoiseFilter; got != "off" {
		t.Errorf("the catalog defaults state noise filter %q, want off", got)
	}
	if strings.Count(string(raw), `"noiseFilter"`) != 1 {
		t.Error("a layer other than the defaults states a noise filter")
	}
}
