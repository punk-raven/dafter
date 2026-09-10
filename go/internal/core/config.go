package core

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

type PrivacyMode string

const (
	PrivacyOpen         PrivacyMode = "open"
	PrivacySealed       PrivacyMode = "sealed"
	PrivacyTrustedAgent PrivacyMode = "trusted_agent"
)

type Channel string

const (
	ChannelWebRTC    Channel = "webrtc"
	ChannelTelephony Channel = "telephony"
	ChannelLongForm  Channel = "long_form"
)

type Role string

const (
	RoleParticipant Role = "participant"
	RolePresenter   Role = "presenter"
	RoleObserver    Role = "observer"
	RoleAgent       Role = "agent"
	RoleRecorder    Role = "recorder"
)

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

type AgentMode string

const (
	ModeCascaded       AgentMode = "cascaded"
	ModeHalfCascade    AgentMode = "half_cascade"
	ModeSpeechToSpeech AgentMode = "speech_to_speech"
)

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

type TurnStrategy string

const (
	TurnAuto                TurnStrategy = "auto"
	TurnVAD                 TurnStrategy = "vad"
	TurnProviderEndpointing TurnStrategy = "provider_endpointing"
	TurnSemantic            TurnStrategy = "semantic"
	TurnServerVAD           TurnStrategy = "server_vad"
	TurnManual              TurnStrategy = "manual"
)

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

type EgressLayout string

const (
	LayoutTrack          EgressLayout = "track"
	LayoutTrackComposite EgressLayout = "track_composite"
	LayoutRoomComposite  EgressLayout = "room_composite"
)

type RecordingStart string

const (
	StartAtSessionCreate RecordingStart = "session_create"
	StartAtFirstPublish  RecordingStart = "first_publish"
)

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
