package events

import (
	"errors"
	"slices"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/schema"
)

func event(t EventType, payload map[string]any) *EventEnvelope {
	return &EventEnvelope{
		EventID:    "e_" + "0123456789abcdef0123456789abcdef",
		Type:       t,
		Version:    1,
		SessionID:  "s_7f3a9c21",
		TenantID:   "t_9c21a4be",
		Sequence:   0,
		OccurredAt: time.Date(2026, 9, 11, 10, 0, 0, 0, time.UTC),
		Payload:    payload,
	}
}

func TestEventTypesMatchSchema(t *testing.T) {
	want, err := schema.EnumAt("events/v1/envelope.schema.json", "$defs", "EventType", "enum")
	if err != nil {
		t.Fatal(err)
	}
	got := []string{
		string(EventSessionCreated), string(EventSessionEnded), string(EventSessionSignal),
		string(EventConnectionEstablished), string(EventConnectionLost), string(EventConnectionRestored),
		string(EventAgentDispatched), string(EventAgentStateChanged), string(EventAgentHandoffRequested),
		string(EventTranscriptPartial), string(EventTranscriptFinal), string(EventTranscriptVersionCreated),
		string(EventTranslationFinal), string(EventRecordingStarted), string(EventRecordingCompleted),
		string(EventRecordingSealed), string(EventProviderDegraded), string(EventProviderFailedOver),
		string(EventPolicyViolation), string(EventBudgetExceeded),
	}
	slices.Sort(got)
	if !slices.Equal(got, want) {
		t.Errorf("EventType drift\n go: %v\n schema: %v", got, want)
	}
}

func TestAgentStatesMatchSchema(t *testing.T) {
	want, err := schema.EnumAt("events/v1/envelope.schema.json", "$defs", "AgentState", "enum")
	if err != nil {
		t.Fatal(err)
	}
	got := []string{
		string(AgentIdle), string(AgentListening), string(AgentThinking), string(AgentSpeaking),
	}
	slices.Sort(got)
	if !slices.Equal(got, want) {
		t.Errorf("AgentState drift\n go: %v\n schema: %v", got, want)
	}
}

func TestValidEventPasses(t *testing.T) {
	e := event(EventAgentStateChanged, map[string]any{
		"state": string(AgentThinking), "previousState": string(AgentListening),
	})
	if err := e.Validate(); err != nil {
		t.Fatalf("a well-formed agent.state_changed was rejected: %v", err)
	}
}

func TestTypedPayloadIsEnforced(t *testing.T) {
	cases := []struct {
		name    string
		payload map[string]any
	}{
		{"wrong enum case", map[string]any{"state": "THINKING"}},
		{"invented state", map[string]any{"state": "pondering"}},
		{"missing required state", map[string]any{}},
		{"typo in field name", map[string]any{"state": "thinking", "stat": "x"}},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := event(EventAgentStateChanged, tc.payload).Validate()
			if err == nil {
				t.Fatalf("agent.state_changed accepted %v", tc.payload)
			}
			var de *errs.Error
			if !errors.As(err, &de) || de.Code != errs.CodeInternal {
				t.Fatalf("want %s, got %v", errs.CodeInternal, err)
			}
			if len(de.Details) == 0 {
				t.Errorf("no located problem reported for %v", tc.payload)
			}
		})
	}
}

func TestUntypedEventsStillAcceptAnything(t *testing.T) {
	e := event(EventRecordingSealed, map[string]any{"whatever": []any{1.0, 2.0}})
	if err := e.Validate(); err != nil {
		t.Fatalf("an intentionally untyped event was rejected: %v", err)
	}
}

func TestTimestampsGoProducesAreAccepted(t *testing.T) {
	for _, ts := range []time.Time{
		time.Date(2026, 9, 11, 10, 0, 0, 0, time.UTC),
		time.Date(2026, 9, 11, 10, 0, 0, 123456789, time.UTC),
		time.Date(2026, 9, 11, 15, 30, 0, 0, time.FixedZone("IST", 5*3600+1800)),
	} {
		e := event(EventSessionSignal, map[string]any{"name": "handoff"})
		e.OccurredAt = ts
		if err := e.Validate(); err != nil {
			t.Errorf("timestamp %s was rejected: %v", ts.Format(time.RFC3339Nano), err)
		}
	}
}

func TestEventRejectsANonOpaqueSessionID(t *testing.T) {
	e := event(EventSessionSignal, map[string]any{"name": "handoff"})
	e.SessionID = "call-with-jane@example.com"
	if err := e.Validate(); err == nil {
		t.Fatal("an identifying session id reached an event; these land in vendor dashboards")
	}
}
