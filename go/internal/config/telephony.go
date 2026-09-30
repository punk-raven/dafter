package config

import "time"

const (
	DefaultRingingTimeout  = 30 * time.Second
	DefaultMaxCallDuration = 30 * time.Minute
)

type Telephony struct {
	Trunk                  string          `json:"trunk,omitempty"`
	RingingTimeoutSeconds  int             `json:"ringingTimeoutSeconds,omitempty"`
	MaxCallDurationSeconds int             `json:"maxCallDurationSeconds,omitempty"`
	RecordingNotice        RecordingNotice `json:"recordingNotice,omitempty"`
}

func (c *ResolvedSessionConfig) TrunkName() string {
	if c.Telephony == nil {
		return ""
	}
	return c.Telephony.Trunk
}

func (c *ResolvedSessionConfig) RingingTimeout() time.Duration {
	if c.Telephony == nil || c.Telephony.RingingTimeoutSeconds == 0 {
		return DefaultRingingTimeout
	}
	return time.Duration(c.Telephony.RingingTimeoutSeconds) * time.Second
}

func (c *ResolvedSessionConfig) MaxCallDuration() time.Duration {
	if c.Telephony == nil || c.Telephony.MaxCallDurationSeconds == 0 {
		return DefaultMaxCallDuration
	}
	return time.Duration(c.Telephony.MaxCallDurationSeconds) * time.Second
}
