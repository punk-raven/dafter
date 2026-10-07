package config_test

import (
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

func addressed(t *testing.T, name string, a *config.Addressing) *config.ResolvedSessionConfig {
	t.Helper()
	c := validConfig(t)
	c.Agent.Name = name
	c.Agent.Addressing = a
	return c
}

func rejectedAt(t *testing.T, c *config.ResolvedSessionConfig, pointer string) {
	t.Helper()
	var de *errs.Error
	if err := c.Validate(); !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("want %s, got %v", errs.CodeInvalidConfig, err)
	}
	if !strings.Contains(strings.Join(de.Details, "\n"), pointer) {
		t.Errorf("no detail points at %s: %v", pointer, de.Details)
	}
}

func TestEveryAddressingModeValidatesWithAName(t *testing.T) {
	t.Parallel()
	for _, mode := range config.AllAddressingModes {
		c := addressed(t, "Nivya", &config.Addressing{
			Mode: mode, Aliases: []string{"निव्या"},
			NearMisses: []string{"Navya"}, FollowUpWindowMs: 20000,
		})
		if err := c.Validate(); err != nil {
			t.Errorf("%s rejected: %v", mode, err)
		}
	}
}

func TestAlwaysNeedsNoName(t *testing.T) {
	t.Parallel()
	if err := addressed(t, "", &config.Addressing{Mode: config.AddressingAlways}).Validate(); err != nil {
		t.Fatalf("always without a name rejected: %v", err)
	}
}

func TestAModeThatWaitsToBeCalledNeedsAName(t *testing.T) {
	t.Parallel()
	for _, mode := range []config.AddressingMode{config.AddressingTranscript, config.AddressingOnDevice} {
		rejectedAt(t, addressed(t, "", &config.Addressing{Mode: mode}), "/agent/name")
	}
}

func TestANearMissCannotBeTheNameOrAnAlias(t *testing.T) {
	t.Parallel()
	for _, miss := range []string{"Nivya", "निव्या"} {
		c := addressed(t, "Nivya", &config.Addressing{
			Mode: config.AddressingTranscript, Aliases: []string{"निव्या"},
			NearMisses: []string{"Navya", miss},
		})
		rejectedAt(t, c, "/agent/addressing/nearMisses")
	}
}

func TestAddressingOutsideItsBoundsIsRejected(t *testing.T) {
	t.Parallel()
	cases := map[string]string{
		"no mode":            `{"aliases": ["निव्या"]}`,
		"unknown mode":       `{"mode": "wake_word"}`,
		"a name of its own":  `{"mode": "transcript", "name": "Nivya"}`,
		"window too short":   `{"mode": "transcript", "followUpWindowMs": 999}`,
		"window too long":    `{"mode": "transcript", "followUpWindowMs": 120001}`,
		"duplicate alias":    `{"mode": "transcript", "aliases": ["निव्या", "निव्या"]}`,
		"unknown field":      `{"mode": "transcript", "wakeWord": "Nivya"}`,
		"alias not a string": `{"mode": "transcript", "aliases": [7]}`,
	}
	for name, block := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			doc := map[string]any{}
			raw, err := json.Marshal(validConfig(t))
			if err != nil {
				t.Fatal(err)
			}
			if err := json.Unmarshal(raw, &doc); err != nil {
				t.Fatal(err)
			}
			doc["agent"].(map[string]any)["name"] = "Nivya"
			doc["agent"].(map[string]any)["addressing"] = json.RawMessage(block)
			raw, err = json.Marshal(doc)
			if err != nil {
				t.Fatal(err)
			}
			var de *errs.Error
			if _, err := config.Parse(raw); !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
				t.Fatalf("want %s, got %v", errs.CodeInvalidConfig, err)
			}
		})
	}
}
