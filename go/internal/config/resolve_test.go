package config_test

import (
	"bytes"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

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
		Languages: map[string]config.Axis{
			"hi": {
				Tuning:  json.RawMessage(`{"turn": {"strategy": "provider_endpointing", "localVadEnabled": false}}`),
				Overlay: json.RawMessage(`{"agent": {"pipeline": {"stt": {"provider": "sarvam", "model": "saaras"}}}}`),
			},
			"en-IN": {
				Tuning: json.RawMessage(`{"turn": {"strategy": "semantic", "localVadEnabled": true}}`),
				Overlay: json.RawMessage(`{"agent": {"pipeline": {
					"vad": {"provider": "silero"},
					"stt": {"provider": "sarvam", "model": "saaras"}
				}}}`),
			},
		},
		Channels: map[config.Channel]config.Axis{
			config.ChannelWebRTC: {
				Tuning:  json.RawMessage(`{"turn": {"endpointingDelayMs": 0}}`),
				Overlay: json.RawMessage(`{"media": {"egress": {"width": 1280, "height": 720, "framerate": 30, "videoBitrate": 3000, "videoCodec": "h264_main"}}}`),
			},
			config.ChannelTelephony: {
				Tuning:  json.RawMessage(`{"turn": {"silenceMs": 900}}`),
				Overlay: json.RawMessage(`{"media": {"video": {"enabled": false}, "egress": {"audioBitrate": 64}}}`),
			},
			config.ChannelLongForm: {
				Overlay: json.RawMessage(`{"media": {
					"video": {"resolution": "h540", "maxBitrate": 800000, "maxFramerate": 25},
					"egress": {"width": 1280, "height": 720, "framerate": 30, "videoBitrate": 3000, "videoCodec": "h264_main"}
				}}`),
			},
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

func TestResolveLetsASessionOverrideTuneTheTurnAnAxisSuggests(t *testing.T) {
	t.Parallel()
	req := request()
	req.Language = "hi"
	req.Overrides = json.RawMessage(`{"turn": {"strategy": "semantic", "localVadEnabled": true, "endpointingDelayMs": 300}}`)
	c := resolve(t, req).Config
	if c.Turn.Strategy != config.TurnSemantic || !c.Turn.LocalVADDecidesTurn() {
		t.Errorf("the language axis beat the session override: %s, local VAD deciding %v", c.Turn.Strategy, c.Turn.LocalVADDecidesTurn())
	}
	if c.Turn.EndpointingDelayMs != 300 {
		t.Errorf("the channel axis beat the session override: endpointingDelayMs = %d, want 300", c.Turn.EndpointingDelayMs)
	}
	if c.Agent.Pipeline == nil || c.Agent.Pipeline.STT == nil || c.Agent.Pipeline.STT.Provider != "sarvam" {
		t.Errorf("the language pipeline was lost: %+v", c.Agent.Pipeline)
	}
}

func TestResolveRefusesAnOverrideAnOverlayWouldDrop(t *testing.T) {
	t.Parallel()
	req := request()
	req.Language, req.Channel = "hi", config.ChannelTelephony
	req.Overrides = json.RawMessage(`{
		"agent": {"pipeline": {"stt": {"provider": "deepgram", "model": "saaras"}}},
		"media": {"video": {"enabled": true}}
	}`)
	de := resolveError(t, req)
	if de.Code != errs.CodeInvalidConfig {
		t.Errorf("want %s, got %s", errs.CodeInvalidConfig, de.Code)
	}
	joined := strings.Join(de.Details, "\n")
	for _, pointer := range []string{
		"'/agent/pipeline/stt/provider': the hi language overlay pins this",
		"'/media/video/enabled': the telephony channel overlay pins this",
	} {
		if !strings.Contains(joined, pointer) {
			t.Errorf("no detail points at %s; an override the overlay replaces must not vanish\n%v", pointer, de)
		}
	}
	if strings.Contains(joined, "/model") {
		t.Errorf("an override that agrees with the overlay was refused: %v", de)
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
