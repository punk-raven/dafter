package core

import (
	"errors"
	"testing"
	"time"
)

func event(t EventType, payload map[string]any) *EventEnvelope {
	return &EventEnvelope{
		EventID:    MustNewID(PrefixEvent),
		Type:       t,
		Version:    1,
		SessionID:  "s_7f3a9c21",
		TenantID:   "t_9c21a4be",
		Sequence:   0,
		OccurredAt: time.Date(2026, 9, 11, 10, 0, 0, 0, time.UTC),
		Payload:    payload,
	}
}

func TestValidEventPasses(t *testing.T) {
	e := event(EventAgentStateChanged, map[string]any{
		"state":         string(AgentThinking),
		"previousState": string(AgentListening),
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
			var de *Error
			if !errors.As(err, &de) || de.Code != CodeInvalidConfig {
				t.Fatalf("want a Dafter error, got %v", err)
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
