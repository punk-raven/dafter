package config

import "strings"

const DefaultScribePool = "dafter-scribe"

type Scribe struct {
	Enabled                 bool           `json:"enabled"`
	Pool                    string         `json:"pool,omitempty"`
	ConsentArtifactID       string         `json:"consentArtifactId,omitempty"`
	LLM                     *ProviderRef   `json:"llm,omitempty"`
	Judge                   *ProviderRef   `json:"judge,omitempty"`
	Scoring                 *ScribeScoring `json:"scoring,omitempty"`
	SummaryIntervalMs       int            `json:"summaryIntervalMs,omitempty"`
	AfterCallTimeoutSeconds int            `json:"afterCallTimeoutSeconds,omitempty"`
}

const DefaultScribeMaxScoredTurns = 20

type ScribeScoring struct {
	SampleRate          float64            `json:"sampleRate,omitempty"`
	LanguageSampleRates map[string]float64 `json:"languageSampleRates,omitempty"`
	MaxTurnsPerSession  int                `json:"maxTurnsPerSession,omitempty"`
	KeepFailures        bool               `json:"keepFailures,omitempty"`
}

func (s *ScribeScoring) RateFor(language string) float64 {
	if s == nil {
		return 0
	}
	base, _, _ := strings.Cut(strings.ToLower(language), "-")
	if rate, ok := s.LanguageSampleRates[base]; ok {
		return rate
	}
	return s.SampleRate
}

func (s *ScribeScoring) TurnCap() int {
	if s == nil || s.MaxTurnsPerSession == 0 {
		return DefaultScribeMaxScoredTurns
	}
	return s.MaxTurnsPerSession
}

func (c *ResolvedSessionConfig) ScribeEnabled() bool {
	return c.Scribe != nil && c.Scribe.Enabled
}

func (c *ResolvedSessionConfig) ScribePool() string {
	if c.Scribe == nil || c.Scribe.Pool == "" {
		return DefaultScribePool
	}
	return c.Scribe.Pool
}

func (c *ResolvedSessionConfig) scribeConsent() string {
	if c.Scribe == nil {
		return ""
	}
	return c.Scribe.ConsentArtifactID
}

func (c *ResolvedSessionConfig) scribeLLM() *ProviderRef {
	if c.Scribe == nil {
		return nil
	}
	return c.Scribe.LLM
}
