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

func TestValidateRejectsVideoOnTelephony(t *testing.T) {
	t.Parallel()
	c := validConfig(t)
	c.Channel = config.ChannelTelephony
	var de *errs.Error
	if err := c.Validate(); !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("a telephony session kept video: want %s, got %v", errs.CodeInvalidConfig, err)
	}
	if !strings.Contains(strings.Join(de.Details, "\n"), "/media/video/enabled") {
		t.Errorf("no detail points at /media/video/enabled: %v", de)
	}

	off := false
	c.Media = &config.Media{Video: &config.VideoProfile{Enabled: &off}}
	if err := c.Validate(); err != nil {
		t.Fatalf("telephony with video off rejected: %v", err)
	}
}

func TestValidateRejectsScalabilityModeWithoutALayeredCodec(t *testing.T) {
	t.Parallel()
	c := validConfig(t)
	c.Media = &config.Media{Video: &config.VideoProfile{
		Codec: config.CodecH264, ScalabilityMode: "L3T3_KEY",
	}}
	var de *errs.Error
	if err := c.Validate(); !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("H.264 kept a scalability mode: want %s, got %v", errs.CodeInvalidConfig, err)
	}
	if !strings.Contains(strings.Join(de.Details, "\n"), "/media/video/scalabilityMode") {
		t.Errorf("no detail points at /media/video/scalabilityMode: %v", de)
	}

	c.Media.Video.Codec = config.CodecVp9
	if err := c.Validate(); err != nil {
		t.Fatalf("VP9 with a scalability mode rejected: %v", err)
	}
}

func TestValidateAcceptsEveryNoiseFilterAndNothingElse(t *testing.T) {
	t.Parallel()
	for _, filter := range config.AllNoiseCancellations {
		c := validConfig(t)
		c.Media = &config.Media{Audio: &config.AudioProfile{NoiseCancellation: filter}}
		if err := c.Validate(); err != nil {
			t.Errorf("noise filter %s rejected: %v", filter, err)
		}
	}

	c := validConfig(t)
	c.Media = &config.Media{Audio: &config.AudioProfile{NoiseCancellation: "krisp"}}
	var de *errs.Error
	if err := c.Validate(); !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("a filter nothing implements was accepted: want %s, got %v", errs.CodeInvalidConfig, err)
	}
	if !strings.Contains(strings.Join(de.Details, "\n"), "/media/audio/noiseCancellation") {
		t.Errorf("no detail points at /media/audio/noiseCancellation: %v", de)
	}
}

func TestValidateRejectsAPresetBesideExplicitEncodeFields(t *testing.T) {
	t.Parallel()
	c := validConfig(t)
	c.Media = &config.Media{Egress: &config.EgressProfile{
		Preset: config.PresetH264720p30, VideoBitrate: 3000,
	}}
	var de *errs.Error
	if err := c.Validate(); !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("a preset and a bitrate were both accepted: want %s, got %v", errs.CodeInvalidConfig, err)
	}
	if !strings.Contains(strings.Join(de.Details, "\n"), "/media/egress/preset") {
		t.Errorf("no detail points at /media/egress/preset: %v", de)
	}

	c.Media.Egress.VideoBitrate = 0
	if err := c.Validate(); err != nil {
		t.Fatalf("a preset on its own was rejected: %v", err)
	}
}

func TestValidateRejectsVideoEncodeSettingsOnAnAudioOnlyRecording(t *testing.T) {
	t.Parallel()
	off := false
	c := validConfig(t)
	c.Channel = config.ChannelTelephony
	c.Media = &config.Media{
		Video:  &config.VideoProfile{Enabled: &off},
		Egress: &config.EgressProfile{Width: 1280, Height: 720, AudioBitrate: 64},
	}
	if err := c.Validate(); err != nil {
		t.Fatalf("recording is off, so the profile is inert and must pass: %v", err)
	}

	c.Recording = config.Recording{Enabled: true, Layout: config.LayoutRoomComposite, ConsentArtifactID: "consent_1"}
	var de *errs.Error
	if err := c.Validate(); !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("an audio-only recording kept a video size: want %s, got %v", errs.CodeInvalidConfig, err)
	}
	if !strings.Contains(strings.Join(de.Details, "\n"), "/media/egress") {
		t.Errorf("no detail points at /media/egress: %v", de)
	}

	c.Media.Egress = &config.EgressProfile{AudioBitrate: 64}
	if err := c.Validate(); err != nil {
		t.Fatalf("an audio-only encode on an audio-only recording was rejected: %v", err)
	}
}

func sealedConfig(t *testing.T) *config.ResolvedSessionConfig {
	t.Helper()
	c := validConfig(t)
	c.PrivacyMode = config.PrivacySealed
	c.Agent.Enabled = false
	c.Media = &config.Media{Encryption: &config.EncryptionProfile{
		Mode: config.EncryptionE2EE, KeyModel: config.KeyModelServerShared,
	}}
	return c
}

func TestValidateAcceptsASealedSessionThatStatesEndToEndEncryption(t *testing.T) {
	t.Parallel()
	if err := sealedConfig(t).Validate(); err != nil {
		t.Fatalf("sealed session with e2ee stated rejected: %v", err)
	}
	if !sealedConfig(t).MintsSharedKey() {
		t.Error("the control plane would mint no key for a sealed session under the server_shared model")
	}
}

func TestValidateRejectsAnEncryptionModeThatContradictsThePrivacyMode(t *testing.T) {
	t.Parallel()
	cases := map[string]*config.ResolvedSessionConfig{
		"sealed stating transport": func() *config.ResolvedSessionConfig {
			c := sealedConfig(t)
			c.Media.Encryption.Mode = config.EncryptionTransport
			return c
		}(),
		"sealed stating nothing": func() *config.ResolvedSessionConfig {
			c := sealedConfig(t)
			c.Media = nil
			return c
		}(),
		"trusted_agent stating transport": func() *config.ResolvedSessionConfig {
			c := sealedConfig(t)
			c.PrivacyMode = config.PrivacyTrustedAgent
			c.Agent.Enabled = true
			c.Media.Encryption.Mode = config.EncryptionTransport
			return c
		}(),
		"open stating e2ee": func() *config.ResolvedSessionConfig {
			c := validConfig(t)
			c.Media = &config.Media{Encryption: &config.EncryptionProfile{Mode: config.EncryptionE2EE}}
			return c
		}(),
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			var de *errs.Error
			if err := c.Validate(); !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
				t.Fatalf("want %s, got %v", errs.CodeInvalidConfig, err)
			}
			if !strings.Contains(strings.Join(de.Details, "\n"), "/media/encryption/mode") {
				t.Errorf("no detail points at /media/encryption/mode: %v", de)
			}
		})
	}

	open := validConfig(t)
	if open.EncryptionMode() != config.EncryptionTransport {
		t.Errorf("an open session stating nothing reads as %s, want transport", open.EncryptionMode())
	}
	if err := open.Validate(); err != nil {
		t.Fatalf("open session stating nothing rejected: %v", err)
	}
	if open.MintsSharedKey() {
		t.Error("the control plane would mint a key for an open session")
	}
}

func TestValidateRefusesServerSideRecordingUnderEndToEndEncryption(t *testing.T) {
	t.Parallel()
	for _, mode := range []config.PrivacyMode{config.PrivacySealed, config.PrivacyTrustedAgent} {
		for _, layout := range config.AllEgressLayouts {
			t.Run(string(mode)+"/"+string(layout), func(t *testing.T) {
				t.Parallel()
				c := sealedConfig(t)
				c.PrivacyMode = mode
				c.Recording = config.Recording{Enabled: true, Layout: layout, ConsentArtifactID: "consent_1"}
				if layout == config.LayoutRoomComposite {
					c.Recording.StartAt = config.StartAtSessionCreate
				}
				var de *errs.Error
				if err := c.Validate(); !errors.As(err, &de) || de.Code != errs.CodePrivacyModeForbids {
					t.Fatalf("want %s, got %v", errs.CodePrivacyModeForbids, err)
				}
				if !strings.Contains(strings.Join(de.Details, "\n"), "/recording/enabled") {
					t.Errorf("no detail points at /recording/enabled: %v", de)
				}
			})
		}
	}

	c := validConfig(t)
	c.Recording = config.Recording{Enabled: true, Layout: config.LayoutTrack, ConsentArtifactID: "consent_1"}
	if err := c.Validate(); err != nil {
		t.Fatalf("an open session with recording rejected: %v", err)
	}
}

func TestValidateRefusesALocalVADDecidingAProviderEndpointedTurn(t *testing.T) {
	t.Parallel()
	off, on := false, true
	for _, stated := range []*bool{nil, &on} {
		c := validConfig(t)
		c.Turn = config.Turn{Strategy: config.TurnProviderEndpointing, LocalVADEnabled: stated}
		var de *errs.Error
		err := c.Validate()
		if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig || !strings.Contains(de.Details[0], "/turn/localVadEnabled") {
			t.Errorf("localVadEnabled %v: want a refusal at /turn/localVadEnabled, got %v", stated, err)
		}
	}
	c := validConfig(t)
	c.Turn = config.Turn{
		Strategy:        config.TurnProviderEndpointing,
		LocalVADEnabled: &off,
		Interruption:    &config.Interruption{LocalVADEnabled: &on},
	}
	if err := c.Validate(); err != nil {
		t.Errorf("a local VAD that only catches barge-in was refused: %v", err)
	}
}

func TestKeyDisclosureFollowsThePrivacyModeAndTheRole(t *testing.T) {
	t.Parallel()
	humans := []config.Role{config.RoleParticipant, config.RolePresenter, config.RoleObserver}
	for _, role := range config.AllRoles {
		if config.PrivacyOpen.DisclosesKeyTo(role) {
			t.Errorf("open discloses a key to %s; there is none", role)
		}
	}
	for _, role := range humans {
		if !config.PrivacySealed.DisclosesKeyTo(role) || !config.PrivacyTrustedAgent.DisclosesKeyTo(role) {
			t.Errorf("%s is not handed the key in an end-to-end encrypted session", role)
		}
	}
	if config.PrivacySealed.DisclosesKeyTo(config.RoleAgent) {
		t.Error("sealed handed the key to an agent; the mode exists so that only the humans can decrypt")
	}
	if !config.PrivacyTrustedAgent.DisclosesKeyTo(config.RoleAgent) {
		t.Error("trusted_agent withheld the key from the agent it names")
	}
	for _, mode := range config.AllPrivacyModes {
		if mode.DisclosesKeyTo(config.RoleRecorder) {
			t.Errorf("%s handed the key to a recorder; a server-side egress sees only ciphertext by design", mode)
		}
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
		{"AddressingMode", cfg, []string{"$defs", "Addressing", "properties", "mode", "enum"}, schema.Names(config.AllAddressingModes)},
		{"TurnStrategy", cfg, []string{"$defs", "Turn", "properties", "strategy", "enum"}, schema.Names(config.AllTurnStrategies)},
		{"VideoCodec", cfg, []string{"$defs", "VideoProfile", "properties", "codec", "enum"}, schema.Names(config.AllVideoCodecs)},
		{"VideoResolution", cfg, []string{"$defs", "VideoProfile", "properties", "resolution", "enum"}, schema.Names(config.AllVideoResolutions)},
		{"NoiseCancellation", cfg, []string{"$defs", "AudioProfile", "properties", "noiseCancellation", "enum"}, schema.Names(config.AllNoiseCancellations)},
		{"EgressPreset", cfg, []string{"$defs", "EgressProfile", "properties", "preset", "enum"}, schema.Names(config.AllEgressPresets)},
		{"EgressVideoCodec", cfg, []string{"$defs", "EgressProfile", "properties", "videoCodec", "enum"}, schema.Names(config.AllEgressVideoCodecs)},
		{"EncryptionMode", cfg, []string{"$defs", "EncryptionProfile", "properties", "mode", "enum"}, schema.Names(config.AllEncryptionModes)},
		{"KeyModel", cfg, []string{"$defs", "EncryptionProfile", "properties", "keyModel", "enum"}, schema.Names(config.AllKeyModels)},
		{"EgressLayout", cfg, []string{"$defs", "Recording", "properties", "layout", "enum"}, schema.Names(config.AllEgressLayouts)},
		{"RecordingStart", cfg, []string{"$defs", "Recording", "properties", "startAt", "enum"}, schema.Names(config.AllRecordingStarts)},
		{"TranscriptionMode", cfg, []string{"$defs", "Transcription", "properties", "mode", "enum"}, schema.Names(config.AllTranscriptionModes)},
		{"SpeechNormalization", cfg, []string{"$defs", "Speech", "properties", "normalization", "enum"}, schema.Names(config.AllSpeechNormalizations)},
		{"Situation", cfg, []string{"$defs", "Situation", "enum"}, schema.Names(config.AllSituations)},
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
