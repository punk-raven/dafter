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
	Media     *Media     `json:"media,omitempty"`
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

type Media struct {
	Video      *VideoProfile      `json:"video,omitempty"`
	Audio      *AudioProfile      `json:"audio,omitempty"`
	Egress     *EgressProfile     `json:"egress,omitempty"`
	Encryption *EncryptionProfile `json:"encryption,omitempty"`
}

type VideoProfile struct {
	Enabled         *bool           `json:"enabled,omitempty"`
	Codec           VideoCodec      `json:"codec,omitempty"`
	BackupCodec     VideoCodec      `json:"backupCodec,omitempty"`
	ScalabilityMode string          `json:"scalabilityMode,omitempty"`
	Resolution      VideoResolution `json:"resolution,omitempty"`
	MaxBitrate      int             `json:"maxBitrate,omitempty"`
	MaxFramerate    int             `json:"maxFramerate,omitempty"`
	Simulcast       *bool           `json:"simulcast,omitempty"`
	Dynacast        *bool           `json:"dynacast,omitempty"`
	AdaptiveStream  *bool           `json:"adaptiveStream,omitempty"`
}

type AudioProfile struct {
	RED               *bool             `json:"red,omitempty"`
	DTX               *bool             `json:"dtx,omitempty"`
	EchoCancellation  *bool             `json:"echoCancellation,omitempty"`
	NoiseCancellation NoiseCancellation `json:"noiseCancellation,omitempty"`
}

func (c *ResolvedSessionConfig) VideoEnabled() bool {
	if c.Media == nil || c.Media.Video == nil || c.Media.Video.Enabled == nil {
		return true
	}
	return *c.Media.Video.Enabled
}

func (c *ResolvedSessionConfig) video() *VideoProfile {
	if c.Media == nil {
		return nil
	}
	return c.Media.Video
}

func (v VideoCodec) Layered() bool {
	return v == CodecVp9 || v == CodecAv1
}

// EgressProfile is how a composite recording is encoded. Bitrates are in
// kbps, the unit the egress API takes, and are not the bps of VideoProfile.
type EgressProfile struct {
	Preset       EgressPreset     `json:"preset,omitempty"`
	Width        int              `json:"width,omitempty"`
	Height       int              `json:"height,omitempty"`
	Framerate    int              `json:"framerate,omitempty"`
	VideoBitrate int              `json:"videoBitrate,omitempty"`
	AudioBitrate int              `json:"audioBitrate,omitempty"`
	VideoCodec   EgressVideoCodec `json:"videoCodec,omitempty"`
}

// StatesVideo reports whether the profile names any video encode setting,
// the preset included: every preset is a video preset.
func (e *EgressProfile) StatesVideo() bool {
	return e != nil && (e.Preset != "" || e.Width != 0 || e.Height != 0 ||
		e.Framerate != 0 || e.VideoBitrate != 0 || e.VideoCodec != "")
}

// StatesExplicitFields reports whether the profile spells out any encode
// setting rather than naming a preset.
func (e *EgressProfile) StatesExplicitFields() bool {
	return e != nil && (e.Width != 0 || e.Height != 0 || e.Framerate != 0 ||
		e.VideoBitrate != 0 || e.AudioBitrate != 0 || e.VideoCodec != "")
}

func (c *ResolvedSessionConfig) Egress() *EgressProfile {
	if c.Media == nil {
		return nil
	}
	return c.Media.Egress
}

type EncryptionProfile struct {
	Mode     EncryptionMode `json:"mode,omitempty"`
	KeyModel KeyModel       `json:"keyModel,omitempty"`
}

// Encryption is the mode the privacy mode implies. Resolution stamps it into
// the media profile, and a layer stating a different one is a cross-field error.
func (m PrivacyMode) Encryption() EncryptionMode {
	if m == PrivacyOpen {
		return EncryptionTransport
	}
	return EncryptionE2EE
}

// DisclosesKeyTo reports whether a participant in this role is handed the
// session's shared media key. The agent role gets it only where the mode says
// so by name: trusted_agent is the disclosed exception, and sealed means the
// humans alone can decrypt.
func (m PrivacyMode) DisclosesKeyTo(role Role) bool {
	switch role {
	case RoleParticipant, RolePresenter, RoleObserver:
		return m != PrivacyOpen
	case RoleAgent:
		return m == PrivacyTrustedAgent
	default:
		return false
	}
}

// EncryptionMode is what the media profile states, transport when it states
// nothing, because transport encryption is what a media server does unasked.
func (c *ResolvedSessionConfig) EncryptionMode() EncryptionMode {
	if c.Media == nil || c.Media.Encryption == nil || c.Media.Encryption.Mode == "" {
		return EncryptionTransport
	}
	return c.Media.Encryption.Mode
}

// MintsSharedKey reports whether the control plane mints this session's media
// key: end-to-end encryption under the server_shared key model. Another key
// model is one nobody but the consumer holds, and the control plane mints
// nothing for it.
func (c *ResolvedSessionConfig) MintsSharedKey() bool {
	return c.EncryptionMode() == EncryptionE2EE &&
		c.Media != nil && c.Media.Encryption != nil &&
		c.Media.Encryption.KeyModel == KeyModelServerShared
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
