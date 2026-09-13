package config

import (
	"bytes"
	"encoding/json"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/schema"
)

type ResolvedSessionConfig struct {
	APIVersion string `json:"apiVersion"`
	SessionID  string `json:"sessionId"`
	TenantID   string `json:"tenantId"`

	// SHA-256 over the RFC 8785 canonicalization, ConfigHash omitted. Swapping the
	// scheme stops every issued hash reproducing.
	ConfigHash string `json:"configHash,omitempty"`

	PrivacyMode PrivacyMode `json:"privacyMode"`
	Language    string      `json:"language"`
	Channel     Channel     `json:"channel"`

	Residency *Residency `json:"residency,omitempty"`
	Agent     Agent      `json:"agent"`
	Turn      Turn       `json:"turn"`
	Recording Recording  `json:"recording"`
	Budgets   Budgets    `json:"budgets"`
}

type Residency struct {
	AllowedRegions []string `json:"allowedRegions"`
}

type Agent struct {
	Enabled    bool      `json:"enabled"`
	Pool       string    `json:"pool"`
	Mode       AgentMode `json:"mode,omitempty"`
	PersonaRef string    `json:"personaRef,omitempty"`
	Pipeline   *Pipeline `json:"pipeline,omitempty"`
}

type ProviderRef struct {
	Provider      string         `json:"provider"`
	Model         string         `json:"model,omitempty"`
	Region        string         `json:"region,omitempty"`
	CredentialRef string         `json:"credentialRef,omitempty"`
	Options       map[string]any `json:"options,omitempty"`
}

type Pipeline struct {
	VAD      *ProviderRef `json:"vad,omitempty"`
	STT      *ProviderRef `json:"stt,omitempty"`
	LLM      *ProviderRef `json:"llm,omitempty"`
	TTS      *ProviderRef `json:"tts,omitempty"`
	MT       *ProviderRef `json:"mt,omitempty"`
	Realtime *ProviderRef `json:"realtime,omitempty"`
}

type Turn struct {
	Strategy           TurnStrategy `json:"strategy"`
	SilenceMs          int          `json:"silenceMs,omitempty"`
	MinSpeechMs        int          `json:"minSpeechMs,omitempty"`
	EndpointingDelayMs int          `json:"endpointingDelayMs,omitempty"`

	LocalVADEnabled *bool `json:"localVadEnabled,omitempty"`

	Interruption *Interruption `json:"interruption,omitempty"`
}

type Interruption struct {
	Enabled                    *bool `json:"enabled,omitempty"`
	MinDurationMs              int   `json:"minDurationMs,omitempty"`
	MinWords                   int   `json:"minWords,omitempty"`
	FalseInterruptionTimeoutMs int   `json:"falseInterruptionTimeoutMs,omitempty"`
	ResumeFalseInterruption    *bool `json:"resumeFalseInterruption,omitempty"`
}

type Recording struct {
	Enabled           bool           `json:"enabled"`
	Layout            EgressLayout   `json:"layout,omitempty"`
	StartAt           RecordingStart `json:"startAt,omitempty"`
	RetentionClass    string         `json:"retentionClass,omitempty"`
	ConsentArtifactID string         `json:"consentArtifactId,omitempty"`
}

type Budgets struct {
	TurnGapP50Ms      int     `json:"turnGapP50Ms"`
	TurnGapP95Ms      int     `json:"turnGapP95Ms"`
	MaxSessionCostUSD float64 `json:"maxSessionCostUsd,omitempty"`
}

func (c *ResolvedSessionConfig) Validate() error {
	if err := schema.ValidateAgainst(schema.ResolvedSessionConfig, c, errs.CodeInvalidConfig); err != nil {
		return err
	}
	return c.validateCrossFieldRules()
}

func Parse(raw []byte) (*ResolvedSessionConfig, error) {
	if err := schema.ValidateDocument(schema.ResolvedSessionConfig, raw, errs.CodeInvalidConfig); err != nil {
		return nil, err
	}
	var c ResolvedSessionConfig
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(&c); err != nil {
		return nil, errs.Wrap(errs.CodeInvalidConfig, err, "decode resolved session config")
	}
	if err := c.validateCrossFieldRules(); err != nil {
		return nil, err
	}
	return &c, nil
}
