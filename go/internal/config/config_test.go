package config_test

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
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

const (
	tenantID  = "t_9c21a4be"
	sessionID = "s_7f3a9c21"
)

func catalog() *config.Catalog {
	return &config.Catalog{
		Defaults: json.RawMessage(`{
			"apiVersion": "dafter.dev/v1",
			"privacyMode": "open",
			"agent": {"enabled": true, "pool": "dafter-py", "mode": "cascaded"},
			"turn": {"strategy": "auto", "silenceMs": 500, "minSpeechMs": 120},
			"media": {
				"video": {
					"enabled": true, "codec": "vp9", "backupCodec": "h264",
					"scalabilityMode": "L3T3_KEY", "resolution": "h720",
					"maxBitrate": 1700000, "maxFramerate": 30,
					"simulcast": true, "dynacast": true, "adaptiveStream": true
				},
				"audio": {"red": true, "dtx": true, "echoCancellation": true, "noiseCancellation": "native"},
				"egress": {"audioBitrate": 128}
			},
			"recording": {"enabled": false},
			"budgets": {"turnGapP50Ms": 800, "turnGapP95Ms": 1500}
		}`),
		Tenants: map[string]json.RawMessage{
			tenantID: json.RawMessage(`{"budgets": {"turnGapP95Ms": 1200}}`),
		},
		Profiles: map[string]json.RawMessage{
			"support": json.RawMessage(`{"agent": {"personaRef": "persona://support/v3"}}`),
		},
		Languages: map[string]json.RawMessage{
			"hi": json.RawMessage(`{
				"turn": {"strategy": "provider_endpointing", "localVadEnabled": false},
				"agent": {"pipeline": {"stt": {"provider": "sarvam", "model": "saaras"}}}
			}`),
			"en-IN": json.RawMessage(`{
				"turn": {"strategy": "semantic", "localVadEnabled": true},
				"agent": {"pipeline": {
					"vad": {"provider": "silero"},
					"stt": {"provider": "deepgram", "model": "nova"}
				}}
			}`),
		},
		Channels: map[config.Channel]json.RawMessage{
			config.ChannelWebRTC: json.RawMessage(`{
				"turn": {"endpointingDelayMs": 0},
				"media": {"egress": {"width": 1280, "height": 720, "framerate": 30, "videoBitrate": 3000, "videoCodec": "h264_main"}}
			}`),
			config.ChannelTelephony: json.RawMessage(`{
				"turn": {"silenceMs": 900},
				"media": {"video": {"enabled": false}, "egress": {"audioBitrate": 64}}
			}`),
			config.ChannelLongForm: json.RawMessage(`{
				"media": {
					"video": {"resolution": "h540", "maxBitrate": 800000, "maxFramerate": 25},
					"egress": {"width": 1280, "height": 720, "framerate": 30, "videoBitrate": 3000, "videoCodec": "h264_main"}
				}
			}`),
		},
	}
}

func request() config.Request {
	return config.Request{
		SessionID: sessionID,
		TenantID:  tenantID,
		Profile:   "support",
		Language:  "en-IN",
		Channel:   config.ChannelWebRTC,
	}
}

func resolve(t *testing.T, req config.Request) *config.Resolution {
	t.Helper()
	res, err := catalog().Resolve(req)
	if err != nil {
		t.Fatalf("resolve %s/%s: %v", req.Language, req.Channel, err)
	}
	return res
}

func TestResolveAppliesLayersInOrder(t *testing.T) {
	t.Parallel()
	req := request()
	req.Overrides = json.RawMessage(`{"budgets": {"maxSessionCostUsd": 2.5}}`)
	c := resolve(t, req).Config

	if c.Budgets.TurnGapP50Ms != 800 {
		t.Errorf("defaults layer lost: p50 = %d, want 800", c.Budgets.TurnGapP50Ms)
	}
	if c.Budgets.TurnGapP95Ms != 1200 {
		t.Errorf("tenant layer did not win over defaults: p95 = %d, want 1200", c.Budgets.TurnGapP95Ms)
	}
	if c.Agent.PersonaRef != "persona://support/v3" {
		t.Errorf("profile layer lost: personaRef = %q", c.Agent.PersonaRef)
	}
	if c.Budgets.MaxSessionCostUSD != 2.5 {
		t.Errorf("session override lost: maxSessionCostUsd = %v", c.Budgets.MaxSessionCostUSD)
	}
	if c.SessionID != sessionID || c.TenantID != tenantID {
		t.Errorf("identity not stamped from the request: %s/%s", c.SessionID, c.TenantID)
	}
}

func TestResolveTakesTheMediaProfileFromTheChannelAxis(t *testing.T) {
	t.Parallel()
	web := resolve(t, request()).Config

	if !web.VideoEnabled() {
		t.Fatal("webrtc resolved without video")
	}
	if web.Media.Video.Codec != config.CodecVp9 || web.Media.Video.BackupCodec != config.CodecH264 {
		t.Errorf("defaults layer lost: codec %s, backup %s", web.Media.Video.Codec, web.Media.Video.BackupCodec)
	}
	if web.Media.Video.MaxBitrate != 1700000 || web.Media.Video.Resolution != config.ResolutionH720 {
		t.Errorf("720p ceiling lost: %s at %d bps", web.Media.Video.Resolution, web.Media.Video.MaxBitrate)
	}

	tel := request()
	tel.Language = "hi"
	tel.Channel = config.ChannelTelephony
	if telephony := resolve(t, tel).Config; telephony.VideoEnabled() {
		t.Error("telephony resolved with video; the channel carries 8 kHz audio and no video at all")
	}

	long := request()
	long.Channel = config.ChannelLongForm
	lf := resolve(t, long).Config
	if lf.Media.Video.Resolution != config.ResolutionH540 || lf.Media.Video.MaxBitrate != 800000 {
		t.Errorf("long_form overlay lost: %s at %d bps", lf.Media.Video.Resolution, lf.Media.Video.MaxBitrate)
	}
	if lf.Media.Video.Codec != config.CodecVp9 {
		t.Errorf("long_form overlay replaced the profile instead of overlaying it: codec = %s", lf.Media.Video.Codec)
	}
}

func TestResolveTakesTheEgressProfileFromTheChannelAxis(t *testing.T) {
	t.Parallel()
	web := resolve(t, request()).Config
	e := web.Egress()
	if e == nil || e.Width != 1280 || e.Height != 720 || e.Framerate != 30 || e.VideoBitrate != 3000 {
		t.Fatalf("webrtc resolved without the composite encode: %+v", e)
	}
	if e.VideoCodec != config.EgressCodecH264Main || e.AudioBitrate != 128 {
		t.Errorf("codec %s at %d kbps audio; the defaults layer or the channel overlay was lost", e.VideoCodec, e.AudioBitrate)
	}

	tel := request()
	tel.Language, tel.Channel = "hi", config.ChannelTelephony
	tel.Overrides = json.RawMessage(`{"recording": {"enabled": true, "layout": "room_composite", "consentArtifactId": "consent_1"}}`)
	telephony := resolve(t, tel).Config
	if te := telephony.Egress(); te == nil || te.StatesVideo() || te.AudioBitrate != 64 {
		t.Errorf("a telephony recording resolved with a video encode: %+v; the merge cannot delete a key, so the video encode has to live on the channels that carry video", te)
	}
}

func TestResolveRejectsAVideoEncodeOnATelephonyRecording(t *testing.T) {
	t.Parallel()
	req := request()
	req.Language, req.Channel = "hi", config.ChannelTelephony
	req.Overrides = json.RawMessage(`{
		"recording": {"enabled": true, "layout": "room_composite", "consentArtifactId": "consent_1"},
		"media": {"egress": {"width": 1280, "height": 720}}
	}`)
	de := resolveError(t, req)
	if !strings.Contains(strings.Join(de.Details, "\n"), "/media/egress") {
		t.Errorf("no detail points at /media/egress: %v", de)
	}
}

func TestResolveDefersResolutionToTheClientWithoutDeferringBandwidth(t *testing.T) {
	t.Parallel()
	req := request()
	req.Overrides = json.RawMessage(`{"media": {"video": {"resolution": "auto"}}}`)
	c := resolve(t, req).Config

	if c.Media.Video.Resolution != config.ResolutionAuto {
		t.Fatalf("resolution = %s, want %s", c.Media.Video.Resolution, config.ResolutionAuto)
	}
	if c.Media.Video.MaxBitrate != 1700000 || c.Media.Video.MaxFramerate != 30 {
		t.Errorf("caps lost with an auto resolution: %d bps, %d fps",
			c.Media.Video.MaxBitrate, c.Media.Video.MaxFramerate)
	}
	if c.Media.Video.Codec != config.CodecVp9 {
		t.Errorf("rest of the profile lost: codec = %s", c.Media.Video.Codec)
	}
}

func TestResolveStatesTheEncryptionModeThePrivacyModeImplies(t *testing.T) {
	t.Parallel()
	open := resolve(t, request()).Config
	if open.Media.Encryption == nil || open.Media.Encryption.Mode != config.EncryptionTransport {
		t.Fatalf("an open session resolved without stating transport encryption: %+v", open.Media.Encryption)
	}
	if open.Media.Encryption.KeyModel != "" {
		t.Errorf("an open session names a key model %q; there is no key", open.Media.Encryption.KeyModel)
	}

	req := request()
	req.Overrides = json.RawMessage(`{"privacyMode": "sealed", "agent": {"enabled": false}}`)
	sealed := resolve(t, req).Config
	if sealed.Media.Encryption == nil || sealed.Media.Encryption.Mode != config.EncryptionE2EE {
		t.Fatalf("a sealed session resolved without stating e2ee: %+v", sealed.Media.Encryption)
	}
	if sealed.Media.Encryption.KeyModel != config.KeyModelServerShared {
		t.Errorf("key model %q, want %s", sealed.Media.Encryption.KeyModel, config.KeyModelServerShared)
	}
	if !sealed.MintsSharedKey() {
		t.Error("the resolved sealed session would get no key minted")
	}
	if sealed.Media.Video == nil || sealed.Media.Video.Codec != config.CodecVp9 {
		t.Error("stamping the encryption mode replaced the media profile instead of adding to it")
	}

	req.Overrides = json.RawMessage(`{"privacyMode": "trusted_agent"}`)
	trusted := resolve(t, req).Config
	if trusted.Media.Encryption == nil || trusted.Media.Encryption.Mode != config.EncryptionE2EE || !trusted.Agent.Enabled {
		t.Errorf("trusted_agent resolved as %+v with agent %v", trusted.Media.Encryption, trusted.Agent.Enabled)
	}
}

func TestResolveRefusesALayerThatContradictsThePrivacyMode(t *testing.T) {
	t.Parallel()
	req := request()
	req.Overrides = json.RawMessage(`{
		"privacyMode": "sealed", "agent": {"enabled": false},
		"media": {"encryption": {"mode": "transport"}}
	}`)
	de := resolveError(t, req)
	if de.Code != errs.CodeInvalidConfig {
		t.Errorf("want %s, got %s", errs.CodeInvalidConfig, de.Code)
	}
	if !strings.Contains(strings.Join(de.Details, "\n"), "/media/encryption/mode") {
		t.Errorf("no detail points at /media/encryption/mode: %v", de)
	}
}

func TestResolveRefusesRecordingInASealedSession(t *testing.T) {
	t.Parallel()
	req := request()
	req.Overrides = json.RawMessage(`{
		"privacyMode": "sealed", "agent": {"enabled": false},
		"recording": {"enabled": true, "layout": "track", "consentArtifactId": "consent_1"}
	}`)
	de := resolveError(t, req)
	if de.Code != errs.CodePrivacyModeForbids {
		t.Errorf("want %s, got %s", errs.CodePrivacyModeForbids, de.Code)
	}
	if !strings.Contains(strings.Join(de.Details, "\n"), "/recording/enabled") {
		t.Errorf("no detail points at /recording/enabled: %v", de)
	}
}

func TestResolveTakesTurnStrategyFromTheLanguageAxis(t *testing.T) {
	t.Parallel()
	hi := request()
	hi.Language = "hi"
	en := request()

	hindi, english := resolve(t, hi).Config, resolve(t, en).Config
	if hindi.Turn.Strategy == english.Turn.Strategy {
		t.Fatalf("both languages resolved %s; a semantic detector is English-trained and degrades elsewhere", hindi.Turn.Strategy)
	}
	if hindi.Turn.Strategy != config.TurnProviderEndpointing {
		t.Errorf("hi resolved %s, want %s", hindi.Turn.Strategy, config.TurnProviderEndpointing)
	}
	if english.Turn.Strategy != config.TurnSemantic {
		t.Errorf("en-IN resolved %s, want %s", english.Turn.Strategy, config.TurnSemantic)
	}
	if hindi.Turn.LocalVADEnabled == nil || *hindi.Turn.LocalVADEnabled {
		t.Error("hi kept local VAD on; it fights the provider's server VAD over the same audio")
	}
	if hindi.Turn.SilenceMs != 500 || english.Turn.SilenceMs != 500 {
		t.Error("the language overlay dropped a base turn constant it does not set")
	}
}

func TestResolveComposesLanguageAndChannelWithoutACopy(t *testing.T) {
	t.Parallel()
	req := request()
	req.Language, req.Channel = "hi", config.ChannelTelephony
	c := resolve(t, req).Config

	if c.Turn.Strategy != config.TurnProviderEndpointing {
		t.Errorf("language axis lost under a channel overlay: %s", c.Turn.Strategy)
	}
	if c.Turn.SilenceMs != 900 {
		t.Errorf("channel axis lost: silenceMs = %d, want 900", c.Turn.SilenceMs)
	}
	if c.Agent.Pipeline == nil || c.Agent.Pipeline.STT == nil || c.Agent.Pipeline.STT.Provider != "sarvam" {
		t.Errorf("language pipeline lost: %+v", c.Agent.Pipeline)
	}
}

func TestResolveIsDeterministic(t *testing.T) {
	t.Parallel()
	first, second := resolve(t, request()), resolve(t, request())
	if !bytes.Equal(first.Document, second.Document) {
		t.Fatalf("same input resolved to two documents:\n%s\n%s", first.Document, second.Document)
	}
}

func resolveError(t *testing.T, req config.Request) *errs.Error {
	t.Helper()
	return resolveErrorIn(t, catalog(), req)
}

func resolveErrorIn(t *testing.T, cat *config.Catalog, req config.Request) *errs.Error {
	t.Helper()
	res, err := cat.Resolve(req)
	if err == nil {
		t.Fatalf("resolution accepted a request it should reject: %s", res.Document)
	}
	var de *errs.Error
	if !errors.As(err, &de) {
		t.Fatalf("want *errs.Error, got %v", err)
	}
	return de
}

func TestResolveRejectsAnUnsupportedLanguage(t *testing.T) {
	t.Parallel()
	req := request()
	req.Language = "cy"
	de := resolveError(t, req)
	if de.Code != errs.CodeUnsupportedCapability {
		t.Errorf("want %s, got %s", errs.CodeUnsupportedCapability, de.Code)
	}
	if !strings.Contains(strings.Join(de.Details, "\n"), "/language") {
		t.Errorf("no detail points at /language: %v", de)
	}
}

func TestResolveRejectsAnUnsupportedChannel(t *testing.T) {
	t.Parallel()
	cat := catalog()
	delete(cat.Channels, config.ChannelLongForm)

	req := request()
	req.Channel = config.ChannelLongForm
	de := resolveErrorIn(t, cat, req)
	if de.Code != errs.CodeUnsupportedCapability {
		t.Errorf("want %s, got %s", errs.CodeUnsupportedCapability, de.Code)
	}
	if !strings.Contains(strings.Join(de.Details, "\n"), "/channel") {
		t.Errorf("no detail points at /channel: %v", de)
	}
}

func TestResolveNamesEveryUnresolvableLayer(t *testing.T) {
	t.Parallel()
	req := request()
	req.TenantID, req.Profile = "t_00000000", "nonexistent"
	de := resolveError(t, req)
	joined := strings.Join(de.Details, "\n")
	for _, pointer := range []string{"/tenantId", "/profile"} {
		if !strings.Contains(joined, pointer) {
			t.Errorf("no detail points at %s; an operator fixes one layer per round trip\n%v", pointer, de)
		}
	}
}

func TestResolveRejectsConsumerSuppliedIdentity(t *testing.T) {
	t.Parallel()
	req := request()
	req.Overrides = json.RawMessage(`{"sessionId": "s_deadbeef", "configHash": "` + strings.Repeat("0", 64) + `"}`)
	de := resolveError(t, req)
	joined := strings.Join(de.Details, "\n")
	for _, pointer := range []string{"/sessionId", "/configHash"} {
		if !strings.Contains(joined, pointer) {
			t.Errorf("no detail points at %s; the control plane mints these\n%v", pointer, de)
		}
	}
}

func TestResolveRejectsAnOverrideThatBreaksACrossFieldRule(t *testing.T) {
	t.Parallel()
	req := request()
	req.Overrides = json.RawMessage(`{"privacyMode": "sealed"}`)
	de := resolveError(t, req)
	if de.Code != errs.CodePrivacyModeForbids {
		t.Errorf("want %s, got %s", errs.CodePrivacyModeForbids, de.Code)
	}
	if !strings.Contains(strings.Join(de.Details, "\n"), "/agent/enabled") {
		t.Errorf("no detail points at /agent/enabled: %v", de)
	}
}

func TestResolveRejectsAnOverrideTheSchemaForbids(t *testing.T) {
	t.Parallel()
	req := request()
	req.Overrides = json.RawMessage(`{"turn": {"silenceMs": 90000}, "budgets": {"turnGapP50Ms": 0}}`)
	de := resolveError(t, req)
	joined := strings.Join(de.Details, "\n")
	for _, pointer := range []string{"/turn/silenceMs", "/budgets/turnGapP50Ms"} {
		if !strings.Contains(joined, pointer) {
			t.Errorf("no detail points at %s: %v", pointer, de)
		}
	}
}

// Shared with the Python half, which reads the same files. The RFC 8785 vectors are
// vendored from the reference implementation both libraries descend from; see
// testdata/rfc8785/README.md.
const (
	testdataDir    = "../../../testdata"
	rfc8785Vectors = testdataDir + "/rfc8785"
	sharedConfig   = testdataDir + "/config/resolved-session-config.json"
)

func TestCanonicalizeMatchesTheReferenceVectors(t *testing.T) {
	t.Parallel()
	inputs, err := filepath.Glob(filepath.Join(rfc8785Vectors, "input", "*.json"))
	if err != nil || len(inputs) == 0 {
		t.Fatalf("no conformance vectors at %s: %v", rfc8785Vectors, err)
	}
	for _, in := range inputs {
		t.Run(filepath.Base(in), func(t *testing.T) {
			t.Parallel()
			raw, err := os.ReadFile(in)
			if err != nil {
				t.Fatal(err)
			}
			want, err := os.ReadFile(filepath.Join(rfc8785Vectors, "expected", filepath.Base(in)))
			if err != nil {
				t.Fatal(err)
			}
			got, err := config.Canonicalize(raw)
			if err != nil {
				t.Fatalf("canonicalize: %v", err)
			}
			if !bytes.Equal(got, want) {
				t.Errorf("canonicalization differs from the reference\n got: %q\nwant: %q", got, want)
			}
		})
	}
}

func TestResolveStampsTheDocumentWithItsOwnHash(t *testing.T) {
	t.Parallel()
	res := resolve(t, request())

	if res.Config.ConfigHash != res.Hash {
		t.Fatalf("stamped %q but reported %q", res.Config.ConfigHash, res.Hash)
	}
	recomputed, err := config.HashDocument(res.Document)
	if err != nil {
		t.Fatal(err)
	}
	if recomputed != res.Hash {
		t.Errorf("a reader recomputes %s, not the stamped %s; the stored hash is unverifiable", recomputed, res.Hash)
	}
	if canonical, err := config.Canonicalize(res.Document); err != nil || !bytes.Equal(canonical, res.Document) {
		t.Errorf("the stored document is not already canonical: %v", err)
	}
}

func TestHashIgnoresInputSpellingButNotContent(t *testing.T) {
	t.Parallel()
	const spaced = `{"b": 1.0,  "a": [2, {"d": "ö", "c": null}]}`
	const reordered = "{\"a\":[2,{\"c\":null,\"d\":\"ö\"}],\n\"b\":1}"
	const changed = `{"a":[2,{"c":null,"d":"ö"}],"b":2}`

	first, err := config.HashDocument([]byte(spaced))
	if err != nil {
		t.Fatal(err)
	}
	second, err := config.HashDocument([]byte(reordered))
	if err != nil {
		t.Fatal(err)
	}
	if first != second {
		t.Errorf("key order, whitespace or unicode escaping changed the hash: %s vs %s", first, second)
	}
	other, err := config.HashDocument([]byte(changed))
	if err != nil {
		t.Fatal(err)
	}
	if other == first {
		t.Error("a changed value did not change the hash")
	}
}

// The hash is the contract between the Go and Python halves, so it is pinned
// here and to the same literal in python/dafter_core/tests/test_hashing.py.
// Either half drifting fails on its own side rather than at a consumer's audit.
func TestResolvedConfigHashIsPinnedAcrossBothHalves(t *testing.T) {
	t.Parallel()
	raw, err := os.ReadFile(sharedConfig)
	if err != nil {
		t.Fatal(err)
	}
	const want = "afbac94fda3552ab176ee1fa701f7d9f30023591d92ef2f2aceeea5a32a9d156"
	got, err := config.HashDocument(raw)
	if err != nil {
		t.Fatal(err)
	}
	if got != want {
		t.Errorf("hash of the shared vector is %s, want %s", got, want)
	}
	if _, err := config.Parse(raw); err != nil {
		t.Errorf("the shared vector is not a valid resolved config: %v", err)
	}
}

func TestLoadCatalogReadsOneDocument(t *testing.T) {
	t.Parallel()
	raw := []byte(`{
		"defaults": {"apiVersion": "dafter.dev/v1"},
		"tenants": {"t_9c21a4be": {}},
		"profiles": {"support": {}},
		"languages": {"hi": {}, "en-IN": {}},
		"channels": {"webrtc": {}, "telephony": {}}
	}`)
	c, err := config.LoadCatalog(raw)
	if err != nil {
		t.Fatalf("load catalog: %v", err)
	}
	if len(c.Tenants) != 1 || len(c.Profiles) != 1 || len(c.Languages) != 2 || len(c.Channels) != 2 {
		t.Fatalf("catalog loaded %d tenants, %d profiles, %d languages, %d channels",
			len(c.Tenants), len(c.Profiles), len(c.Languages), len(c.Channels))
	}
	if _, ok := c.Languages["en-IN"]; !ok {
		t.Error("a language tag with a region subtag did not survive the key")
	}
	if _, ok := c.Channels[config.ChannelTelephony]; !ok {
		t.Error("telephony did not load")
	}
}

func TestLoadCatalogRejectsWhatCouldNeverResolve(t *testing.T) {
	t.Parallel()
	cases := map[string]string{
		"no defaults layer": `{"tenants": {"t_9c21a4be": {}}, "languages": {"hi": {}}}`,
		"unknown channel":   `{"defaults": {}, "channels": {"carrier_pigeon": {}}}`,
		"unknown section":   `{"defaults": {}, "roles": {"participant": {}}}`,
		"not a JSON object": `["defaults"]`,
	}
	for name, raw := range cases {
		t.Run(name, func(t *testing.T) {
			t.Parallel()
			if _, err := config.LoadCatalog([]byte(raw)); err == nil {
				t.Error("the catalog loaded")
			}
		})
	}
}
