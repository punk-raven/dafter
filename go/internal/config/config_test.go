package config_test

import (
	"errors"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/schema"
)

func validConfig(t *testing.T) *config.ResolvedSessionConfig {
	t.Helper()
	return &config.ResolvedSessionConfig{
		APIVersion:  "dafter.dev/v1",
		SessionID:   "s_7f3a9c21",
		TenantID:    "t_9c21a4be",
		PrivacyMode: config.PrivacyOpen,
		Language:    "en-IN",
		Channel:     config.ChannelWebRTC,
		Agent:       config.Agent{Enabled: true, Pool: "dafter-py", Mode: config.ModeCascaded},
		Turn:        config.Turn{Strategy: config.TurnAuto},
		Recording:   config.Recording{Enabled: false},
		Budgets:     config.Budgets{TurnGapP50Ms: 800, TurnGapP95Ms: 1500},
	}
}

func TestValidateAcceptsAMinimalConfig(t *testing.T) {
	t.Parallel()
	if err := validConfig(t).Validate(); err != nil {
		t.Fatalf("minimal config rejected: %v", err)
	}
}

func TestValidateRejectsAgentInASealedSession(t *testing.T) {
	t.Parallel()
	c := validConfig(t)
	c.PrivacyMode = config.PrivacySealed
	var de *errs.Error
	if err := c.Validate(); !errors.As(err, &de) || de.Code != errs.CodePrivacyModeForbids {
		t.Fatalf("want %s, got %v", errs.CodePrivacyModeForbids, err)
	}
}

func TestValidateRejectsRecordingWithoutConsent(t *testing.T) {
	t.Parallel()
	c := validConfig(t)
	c.Recording = config.Recording{Enabled: true, Layout: config.LayoutTrack}
	var de *errs.Error
	if err := c.Validate(); !errors.As(err, &de) || de.Code != errs.CodeConsentRequired {
		t.Fatalf("want %s, got %v", errs.CodeConsentRequired, err)
	}
}

func TestValidateRejectsImpossibleRecordingStart(t *testing.T) {
	t.Parallel()
	c := validConfig(t)
	c.Recording = config.Recording{
		Enabled: true, Layout: config.LayoutTrack,
		StartAt: config.StartAtSessionCreate, ConsentArtifactID: "consent_1",
	}
	var de *errs.Error
	if err := c.Validate(); !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("want %s, got %v", errs.CodeInvalidConfig, err)
	}
}

func TestValidateNamesEveryBrokenRule(t *testing.T) {
	t.Parallel()
	c := validConfig(t)
	c.PrivacyMode = config.PrivacySealed
	c.Recording = config.Recording{
		Enabled: true, Layout: config.LayoutTrack, StartAt: config.StartAtSessionCreate,
	}

	var de *errs.Error
	if !errors.As(c.Validate(), &de) {
		t.Fatal("want *errs.Error")
	}
	joined := strings.Join(de.Details, "\n")
	for _, pointer := range []string{"/agent/enabled", "/recording/consentArtifactId", "/recording/layout"} {
		if !strings.Contains(joined, pointer) {
			t.Errorf("no detail points at %s; an operator fixes one rule per round trip\n%v", pointer, de)
		}
	}
}

func TestValidateRejectsAConsumerSuppliedSessionID(t *testing.T) {
	t.Parallel()
	c := validConfig(t)
	c.SessionID = "session-for-jane@example.com"
	if err := c.Validate(); err == nil {
		t.Fatal("a non-opaque session id was accepted; identifiers leak into logs and vendor dashboards")
	}
}

func TestValidationNamesEveryProblem(t *testing.T) {
	t.Parallel()
	c := validConfig(t)
	c.APIVersion = "wrong"
	c.SessionID = "not-opaque"
	c.Channel = "carrier-pigeon"
	c.Budgets = config.Budgets{}

	var de *errs.Error
	if !errors.As(c.Validate(), &de) {
		t.Fatal("want *errs.Error")
	}
	joined := strings.Join(de.Details, "\n")
	for _, field := range []string{"/apiVersion", "/sessionId", "/channel", "/budgets/turnGapP50Ms"} {
		if !strings.Contains(joined, field) {
			t.Errorf("no detail mentions %s; an operator cannot act on this\n%v", field, de)
		}
	}
}

func TestErrorSerializesWithinItsOwnSchema(t *testing.T) {
	t.Parallel()
	c := validConfig(t)
	c.Channel = "carrier-pigeon"
	var de *errs.Error
	errors.As(c.Validate(), &de)
	if err := schema.ValidateAgainst(schema.Error, de, errs.CodeInternal); err != nil {
		t.Fatalf("the platform error type does not satisfy the error schema: %v", err)
	}
}

func TestGeneratedEnumsMatchSchema(t *testing.T) {
	t.Parallel()
	const (
		ids = "common/v1/ids.schema.json"
		cfg = "config/v1/resolved-session-config.schema.json"
	)
	cases := []struct {
		name    string
		file    string
		pointer []string
		got     []string
	}{
		{"Role", ids, []string{"$defs", "Role", "enum"}, schema.Names(config.AllRoles)},
		{"Channel", ids, []string{"$defs", "Channel", "enum"}, schema.Names(config.AllChannels)},
		{"PrivacyMode", cfg, []string{"properties", "privacyMode", "enum"}, schema.Names(config.AllPrivacyModes)},
		{"AgentMode", cfg, []string{"properties", "agent", "properties", "mode", "enum"}, schema.Names(config.AllAgentModes)},
		{"TurnStrategy", cfg, []string{"$defs", "Turn", "properties", "strategy", "enum"}, schema.Names(config.AllTurnStrategies)},
		{"EgressLayout", cfg, []string{"$defs", "Recording", "properties", "layout", "enum"}, schema.Names(config.AllEgressLayouts)},
		{"RecordingStart", cfg, []string{"$defs", "Recording", "properties", "startAt", "enum"}, schema.Names(config.AllRecordingStarts)},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			if err := schema.CheckEnum(tc.file, tc.pointer, tc.got); err != nil {
				t.Error(err)
			}
		})
	}
}
