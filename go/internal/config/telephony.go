package config

import "time"

const (
	DefaultRingingTimeout  = 30 * time.Second
	DefaultMaxCallDuration = 30 * time.Minute
)

type Telephony struct {
	Trunk                  string          `json:"trunk,omitempty"`
	PhoneGuests            PhoneGuests     `json:"phoneGuests,omitempty"`
	RingingTimeoutSeconds  int             `json:"ringingTimeoutSeconds,omitempty"`
	MaxCallDurationSeconds int             `json:"maxCallDurationSeconds,omitempty"`
	RecordingNotice        RecordingNotice `json:"recordingNotice,omitempty"`
	DialIn                 *DialIn         `json:"dialIn,omitempty"`
}

type DialIn struct {
	CallerCheck CallerCheck `json:"callerCheck,omitempty"`
}

func (g PhoneGuests) DialsOut() bool { return g == PhoneGuestsDialOut || g == PhoneGuestsBoth }

func (g PhoneGuests) DialsIn() bool { return g == PhoneGuestsDialIn || g == PhoneGuestsBoth }

func (c CallerCheck) AsksForPIN() bool { return c != CallerCheckNumber }

func (c CallerCheck) ChecksNumber() bool { return c != CallerCheckPin }

func (c *ResolvedSessionConfig) TrunkName() string {
	if c.Telephony == nil {
		return ""
	}
	return c.Telephony.Trunk
}

func (c *ResolvedSessionConfig) TakesPhoneCalls() bool {
	return c.TrunkName() != "" && (c.Channel == ChannelTelephony || c.phoneGuests())
}

func (c *ResolvedSessionConfig) phoneGuests() bool {
	return c.Channel != ChannelTelephony && c.Telephony != nil &&
		c.Telephony.PhoneGuests != "" && c.Telephony.PhoneGuests != PhoneGuestsOff
}

func (c *ResolvedSessionConfig) CallsGuestsOut() bool {
	return c.TrunkName() != "" && c.phoneGuests() && c.Telephony.PhoneGuests.DialsOut()
}

func (c *ResolvedSessionConfig) TakesDialIn() bool {
	return c.TrunkName() != "" && c.dialsIn() &&
		c.EncryptionMode() != EncryptionE2EE && c.PrivacyMode == PrivacyOpen
}

func (c *ResolvedSessionConfig) CallerCheck() CallerCheck {
	if c.Telephony == nil || c.Telephony.DialIn == nil || c.Telephony.DialIn.CallerCheck == "" {
		return CallerCheckPin
	}
	return c.Telephony.DialIn.CallerCheck
}

func (c *ResolvedSessionConfig) statesDialInWithoutIt() bool {
	return c.Telephony != nil && c.Telephony.DialIn != nil && !c.dialsIn()
}

func (c *ResolvedSessionConfig) dialsIn() bool {
	return c.phoneGuests() && c.Telephony.PhoneGuests.DialsIn()
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
