package config

import (
	"bytes"
	"encoding/json"
	"slices"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/schema"
)

type ResolvedSessionConfig struct {
	APIVersion string `json:"apiVersion"`
	SessionID  string `json:"sessionId"`
	TenantID   string `json:"tenantId"`

	ConfigHash string `json:"configHash,omitempty"`

	PrivacyMode PrivacyMode `json:"privacyMode"`
	Language    string      `json:"language"`
	Channel     Channel     `json:"channel"`

	Residency *Residency `json:"residency,omitempty"`
	Agent     Agent      `json:"agent"`
	Turn      Turn       `json:"turn"`
	Media     *Media     `json:"media,omitempty"`
	Recording Recording  `json:"recording"`

	Transcription *Transcription `json:"transcription,omitempty"`

	Budgets Budgets `json:"budgets"`
}

type Residency struct {
	AllowedRegions []string `json:"allowedRegions"`
}

type Agent struct {
	Enabled    bool        `json:"enabled"`
	Pool       string      `json:"pool"`
	Name       string      `json:"name,omitempty"`
	Greets     *bool       `json:"greets,omitempty"`
	Mode       AgentMode   `json:"mode,omitempty"`
	PersonaRef string      `json:"personaRef,omitempty"`
	Pipeline   *Pipeline   `json:"pipeline,omitempty"`
	Addressing *Addressing `json:"addressing,omitempty"`
	Speech     *Speech     `json:"speech,omitempty"`
}

type Addressing struct {
	Mode             AddressingMode `json:"mode"`
	Aliases          []string       `json:"aliases,omitempty"`
	NearMisses       []string       `json:"nearMisses,omitempty"`
	FollowUpWindowMs int            `json:"followUpWindowMs,omitempty"`
}

func (a *Addressing) WaitsToBeCalled() bool {
	return a != nil && a.Mode != AddressingAlways
}

func (a Agent) NearMissIsItsName() bool {
	if a.Addressing == nil {
		return false
	}
	for _, miss := range a.Addressing.NearMisses {
		if miss == a.Name || slices.Contains(a.Addressing.Aliases, miss) {
			return true
		}
	}
	return false
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
	Strategy              TurnStrategy `json:"strategy"`
	SilenceMs             int          `json:"silenceMs,omitempty"`
	MinSpeechMs           int          `json:"minSpeechMs,omitempty"`
	EndpointingDelayMs    int          `json:"endpointingDelayMs,omitempty"`
	EndpointingMaxDelayMs int          `json:"endpointingMaxDelayMs,omitempty"`

	LocalVADEnabled *bool `json:"localVadEnabled,omitempty"`

	PreemptiveGeneration *PreemptiveGeneration `json:"preemptiveGeneration,omitempty"`
	Interruption         *Interruption         `json:"interruption,omitempty"`
}

type PreemptiveGeneration struct {
	Enabled *bool `json:"enabled,omitempty"`
	TTS     *bool `json:"tts,omitempty"`
}

type Interruption struct {
	Enabled                    *bool        `json:"enabled,omitempty"`
	LocalVADEnabled            *bool        `json:"localVadEnabled,omitempty"`
	MinDurationMs              int          `json:"minDurationMs,omitempty"`
	MinWords                   int          `json:"minWords,omitempty"`
	FalseInterruptionTimeoutMs int          `json:"falseInterruptionTimeoutMs,omitempty"`
	ResumeFalseInterruption    *bool        `json:"resumeFalseInterruption,omitempty"`
	Backchannel                *Backchannel `json:"backchannel,omitempty"`
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

func (t Turn) LocalVADDecidesTurn() bool {
	return t.LocalVADEnabled == nil || *t.LocalVADEnabled
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

type EgressProfile struct {
	Preset       EgressPreset     `json:"preset,omitempty"`
	Width        int              `json:"width,omitempty"`
	Height       int              `json:"height,omitempty"`
	Framerate    int              `json:"framerate,omitempty"`
	VideoBitrate int              `json:"videoBitrate,omitempty"`
	AudioBitrate int              `json:"audioBitrate,omitempty"`
	VideoCodec   EgressVideoCodec `json:"videoCodec,omitempty"`
}

func (e *EgressProfile) StatesVideo() bool {
	return e != nil && (e.Preset != "" || e.Width != 0 || e.Height != 0 ||
		e.Framerate != 0 || e.VideoBitrate != 0 || e.VideoCodec != "")
}

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

func (m PrivacyMode) Encryption() EncryptionMode {
	if m == PrivacyOpen {
		return EncryptionTransport
	}
	return EncryptionE2EE
}

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

func (c *ResolvedSessionConfig) EncryptionMode() EncryptionMode {
	if c.Media == nil || c.Media.Encryption == nil || c.Media.Encryption.Mode == "" {
		return EncryptionTransport
	}
	return c.Media.Encryption.Mode
}

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

func (r Recording) EffectiveLayout() EgressLayout {
	if r.Layout == "" {
		return LayoutTrack
	}
	return r.Layout
}

type Transcription struct {
	Mode              TranscriptionMode `json:"mode"`
	ConsentArtifactID string            `json:"consentArtifactId,omitempty"`
	Batch             *ProviderRef      `json:"batch,omitempty"`
}

func (c *ResolvedSessionConfig) TranscriptionMode() TranscriptionMode {
	if c.Transcription == nil || c.Transcription.Mode == "" {
		return TranscriptionOff
	}
	return c.Transcription.Mode
}

func (m TranscriptionMode) Transcribes() bool {
	return m != TranscriptionOff
}

func (m TranscriptionMode) Live() bool {
	return m == TranscriptionLive || m == TranscriptionBoth
}

func (m TranscriptionMode) AfterCall() bool {
	return m == TranscriptionAfterCall || m == TranscriptionBoth
}

func (c *ResolvedSessionConfig) transcriptionConsent() string {
	if c.Transcription == nil {
		return ""
	}
	return c.Transcription.ConsentArtifactID
}

func (c *ResolvedSessionConfig) transcriptionBatch() *ProviderRef {
	if c.Transcription == nil {
		return nil
	}
	return c.Transcription.Batch
}

type Budgets struct {
	TurnGapP50Ms      int     `json:"turnGapP50Ms"`
	TurnGapP95Ms      int     `json:"turnGapP95Ms"`
	BargeInStopP50Ms  int     `json:"bargeInStopP50Ms,omitempty"`
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
