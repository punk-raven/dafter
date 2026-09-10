package config

import (
	"errors"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/schema"
)

func validConfig() *ResolvedSessionConfig {
	return &ResolvedSessionConfig{
		APIVersion:  "dafter.dev/v1",
		SessionID:   "s_7f3a9c21",
		TenantID:    "t_9c21a4be",
		PrivacyMode: PrivacyOpen,
		Language:    "en-IN",
		Channel:     ChannelWebRTC,
		Agent:       Agent{Enabled: true, Pool: "dafter-py", Mode: ModeCascaded},
		Turn:        Turn{Strategy: TurnAuto},
		Recording:   Recording{Enabled: false},
		Budgets:     Budgets{TurnGapP50Ms: 800, TurnGapP95Ms: 1500},
	}
}

func TestValidateAcceptsAMinimalConfig(t *testing.T) {
	if err := validConfig().Validate(); err != nil {
		t.Fatalf("minimal config rejected: %v", err)
	}
}

func TestValidateRejectsAgentInASealedSession(t *testing.T) {
	c := validConfig()
	c.PrivacyMode = PrivacySealed
	var de *errs.Error
	if err := c.Validate(); !errors.As(err, &de) || de.Code != errs.CodePrivacyModeForbids {
		t.Fatalf("want %s, got %v", errs.CodePrivacyModeForbids, err)
	}
}

func TestValidateRejectsRecordingWithoutConsent(t *testing.T) {
	c := validConfig()
	c.Recording = Recording{Enabled: true, Layout: LayoutTrack}
	var de *errs.Error
	if err := c.Validate(); !errors.As(err, &de) || de.Code != errs.CodeConsentRequired {
		t.Fatalf("want %s, got %v", errs.CodeConsentRequired, err)
	}
}

func TestValidateRejectsImpossibleRecordingStart(t *testing.T) {
	c := validConfig()
	c.Recording = Recording{
		Enabled: true, Layout: LayoutTrack,
		StartAt: StartAtSessionCreate, ConsentArtifactID: "consent_1",
	}
	var de *errs.Error
	if err := c.Validate(); !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("want %s, got %v", errs.CodeInvalidConfig, err)
	}
}

func TestValidateRejectsAConsumerSuppliedSessionID(t *testing.T) {
	c := validConfig()
	c.SessionID = "session-for-jane@example.com"
	if err := c.Validate(); err == nil {
		t.Fatal("a non-opaque session id was accepted; identifiers leak into logs and vendor dashboards")
	}
}

func TestValidationNamesEveryProblem(t *testing.T) {
	c := validConfig()
	c.APIVersion = "wrong"
	c.SessionID = "not-opaque"
	c.Channel = "carrier-pigeon"
	c.Budgets = Budgets{}

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
	c := validConfig()
	c.Channel = "carrier-pigeon"
	var de *errs.Error
	errors.As(c.Validate(), &de)
	if err := schema.ValidateAgainst(schema.Error, de, errs.CodeInternal); err != nil {
		t.Fatalf("the platform error type does not satisfy the error schema: %v", err)
	}
}
