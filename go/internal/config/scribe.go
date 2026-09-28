package config

const DefaultScribePool = "dafter-scribe"

type Scribe struct {
	Enabled                 bool         `json:"enabled"`
	Pool                    string       `json:"pool,omitempty"`
	ConsentArtifactID       string       `json:"consentArtifactId,omitempty"`
	LLM                     *ProviderRef `json:"llm,omitempty"`
	Judge                   *ProviderRef `json:"judge,omitempty"`
	SummaryIntervalMs       int          `json:"summaryIntervalMs,omitempty"`
	AfterCallTimeoutSeconds int          `json:"afterCallTimeoutSeconds,omitempty"`
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
