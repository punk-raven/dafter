package core

import (
	"encoding/json"
	"errors"
	"slices"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/core/schemagen"
)

func enumAt(t *testing.T, file string, keys ...string) []string {
	t.Helper()
	b, err := schemagen.FS.ReadFile(file)
	if err != nil {
		t.Fatalf("read %s: %v (run `make generate`)", file, err)
	}
	var doc map[string]any
	if err := json.Unmarshal(b, &doc); err != nil {
		t.Fatalf("parse %s: %v", file, err)
	}
	cur := any(doc)
	for _, k := range keys {
		m, ok := cur.(map[string]any)
		if !ok {
			t.Fatalf("%s: %q is not an object", file, strings.Join(keys, "."))
		}
		cur = m[k]
	}
	raw, ok := cur.([]any)
	if !ok {
		t.Fatalf("%s: %q is not an enum", file, strings.Join(keys, "."))
	}
	out := make([]string, 0, len(raw))
	for _, v := range raw {
		out = append(out, v.(string))
	}
	slices.Sort(out)
	return out
}

func assertSameSet(t *testing.T, what string, got, want []string) {
	t.Helper()
	slices.Sort(got)
	if slices.Equal(got, want) {
		return
	}
	for _, v := range want {
		if !slices.Contains(got, v) {
			t.Errorf("%s: schema declares %q but Go does not", what, v)
		}
	}
	for _, v := range got {
		if !slices.Contains(want, v) {
			t.Errorf("%s: Go declares %q but the schema does not", what, v)
		}
	}
}

func TestErrorCodesMatchSchema(t *testing.T) {
	got := []string{
		string(CodeInvalidConfig), string(CodeUnsupportedCapability), string(CodeResidencyViolation),
		string(CodeConsentRequired), string(CodePrivacyModeForbids), string(CodeQuotaExceeded),
		string(CodeBudgetExceeded), string(CodeRateLimited), string(CodeAuthenticationFailed),
		string(CodeProviderUnavailable), string(CodeProviderTimeout), string(CodeStreamClosed),
		string(CodeCancelled), string(CodeInternal),
	}
	want := enumAt(t, "schemas/errors/v1/error.schema.json", "$defs", "ErrorCode", "enum")
	assertSameSet(t, "ErrorCode", got, want)
}

func TestEventTypesMatchSchema(t *testing.T) {
	got := []string{
		string(EventSessionCreated), string(EventSessionEnded), string(EventSessionSignal),
		string(EventConnectionEstablished), string(EventConnectionLost), string(EventConnectionRestored),
		string(EventAgentDispatched), string(EventAgentStateChanged), string(EventAgentHandoffRequested),
		string(EventTranscriptPartial), string(EventTranscriptFinal), string(EventTranscriptVersionCreated),
		string(EventTranslationFinal), string(EventRecordingStarted), string(EventRecordingCompleted),
		string(EventRecordingSealed), string(EventProviderDegraded), string(EventProviderFailedOver),
		string(EventPolicyViolation), string(EventBudgetExceeded),
	}
	want := enumAt(t, "schemas/events/v1/envelope.schema.json", "$defs", "EventType", "enum")
	assertSameSet(t, "EventType", got, want)
}

func TestRolesMatchSchema(t *testing.T) {
	got := []string{
		string(RoleParticipant), string(RolePresenter), string(RoleObserver),
		string(RoleAgent), string(RoleRecorder),
	}
	want := enumAt(t, "schemas/common/v1/ids.schema.json", "$defs", "Role", "enum")
	assertSameSet(t, "Role", got, want)
}

func TestAgentStatesMatchSchema(t *testing.T) {
	got := []string{string(AgentIdle), string(AgentListening), string(AgentThinking), string(AgentSpeaking)}
	want := enumAt(t, "schemas/events/v1/envelope.schema.json", "$defs", "AgentState", "enum")
	assertSameSet(t, "AgentState", got, want)
}

func validConfig() *ResolvedSessionConfig {
	return &ResolvedSessionConfig{
		APIVersion:  "dafter.dev/v1",
		SessionID:   "s_7f3a9c21",
		TenantID:    "t_9c21a4be",
		PrivacyMode: PrivacyOpen,
		Language:    "en-IN",
		Channel:     ChannelWebRTC,
		Agent:       Agent{Enabled: true, Pool: "dafter-py", Mode: ModeCascaded},
		Turn:        Turn{Strategy: TurnAuto},
		Recording:   Recording{Enabled: false},
		Budgets:     Budgets{TurnGapP50Ms: 800, TurnGapP95Ms: 1500},
	}
}

func TestValidateAcceptsAMinimalConfig(t *testing.T) {
	if err := validConfig().Validate(); err != nil {
		t.Fatalf("minimal config rejected: %v", err)
	}
}

func TestValidateRejectsAgentInASealedSession(t *testing.T) {
	c := validConfig()
	c.PrivacyMode = PrivacySealed
	err := c.Validate()
	if err == nil {
		t.Fatal("a sealed session accepted an agent; the trust boundary is not enforced")
	}
	var de *Error
	if !asDafterError(err, &de) || de.Code != CodePrivacyModeForbids {
		t.Fatalf("want %s, got %v", CodePrivacyModeForbids, err)
	}
}

func TestValidateRejectsRecordingWithoutConsent(t *testing.T) {
	c := validConfig()
	c.Recording = Recording{Enabled: true, Layout: LayoutTrack}
	err := c.Validate()
	var de *Error
	if err == nil || !asDafterError(err, &de) || de.Code != CodeConsentRequired {
		t.Fatalf("want %s, got %v", CodeConsentRequired, err)
	}
}

func TestValidateRejectsImpossibleRecordingStart(t *testing.T) {
	c := validConfig()
	c.Recording = Recording{
		Enabled:           true,
		Layout:            LayoutTrack,
		StartAt:           StartAtSessionCreate,
		ConsentArtifactID: "consent_1",
	}
	err := c.Validate()
	var de *Error
	if err == nil || !asDafterError(err, &de) || de.Code != CodeInvalidConfig {
		t.Fatalf("want %s, got %v", CodeInvalidConfig, err)
	}
}

func TestValidateRejectsAConsumerSuppliedSessionID(t *testing.T) {
	c := validConfig()
	c.SessionID = "session-for-jane@example.com"
	if err := c.Validate(); err == nil {
		t.Fatal("a non-opaque session id was accepted; identifiers leak into logs, traces and vendor dashboards")
	}
}

func asDafterError(err error, target **Error) bool {
	return errors.As(err, target)
}
