package config_test

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

func turnDetectorDocument(t *testing.T, detector any) []byte {
	t.Helper()
	raw, err := json.Marshal(validConfig(t))
	if err != nil {
		t.Fatal(err)
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	doc["turn"].(map[string]any)["detector"] = detector
	raw, err = json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestATurnNamesEachDetectorTheSchemaAllows(t *testing.T) {
	t.Parallel()
	for _, detector := range []string{"livekit", "smart_turn"} {
		c, err := config.Parse(turnDetectorDocument(t, detector))
		if err != nil {
			t.Fatalf("%s rejected: %v", detector, err)
		}
		if got := c.Turn.Detector; got != detector {
			t.Errorf("%s decoded as %q", detector, got)
		}
	}
}

func TestATurnDetectorTheSchemaDoesNotAllowIsRefused(t *testing.T) {
	t.Parallel()
	for _, detector := range []any{"multilingual", "", 1} {
		_, err := config.Parse(turnDetectorDocument(t, detector))
		var de *errs.Error
		if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
			t.Fatalf("%v: want %s, got %v", detector, errs.CodeInvalidConfig, err)
		}
		if !strings.Contains(strings.Join(de.Details, "\n"), "/turn/detector") {
			t.Errorf("%v: no detail points at the turn detector: %v", detector, de)
		}
	}
}

func TestASessionOverrideTurnsOnTheSmartTurnTrial(t *testing.T) {
	t.Parallel()
	req := request()
	req.Overrides = json.RawMessage(`{"turn": {"strategy": "semantic", "localVadEnabled": true, "detector": "smart_turn"}}`)
	if got := resolve(t, req).Config.Turn.Detector; got != "smart_turn" {
		t.Errorf("the override's turn detector was not applied: %q", got)
	}
}
