package events_test

import (
	"errors"
	"testing"
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/events"
	"github.com/punk-raven/dafter/go/internal/schema"
)

func event(t *testing.T, typ events.EventType, payload map[string]any) *events.EventEnvelope {
	t.Helper()
	return &events.EventEnvelope{
		EventID:    "e_0123456789abcdef0123456789abcdef",
		Type:       typ,
		Version:    1,
		SessionID:  "s_7f3a9c21",
		TenantID:   "t_9c21a4be",
		Sequence:   0,
		OccurredAt: time.Date(2026, 9, 11, 10, 0, 0, 0, time.UTC),
		Payload:    payload,
	}
}

func TestValidEventPasses(t *testing.T) {
	t.Parallel()
	e := event(t, events.EventAgentStateChanged, map[string]any{
		"state": string(events.AgentThinking), "previousState": string(events.AgentListening),
	})
	if err := e.Validate(); err != nil {
		t.Fatalf("a well-formed agent.state_changed was rejected: %v", err)
	}
}

func TestTypedPayloadIsEnforced(t *testing.T) {
	t.Parallel()
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
			t.Parallel()
			err := event(t, events.EventAgentStateChanged, tc.payload).Validate()
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
	t.Parallel()
	e := event(t, events.EventRecordingSealed, map[string]any{"whatever": []any{1.0, 2.0}})
	if err := e.Validate(); err != nil {
		t.Fatalf("an intentionally untyped event was rejected: %v", err)
	}
}

func TestTimestampsGoProducesAreAccepted(t *testing.T) {
	t.Parallel()
	for _, ts := range []time.Time{
		time.Date(2026, 9, 11, 10, 0, 0, 0, time.UTC),
		time.Date(2026, 9, 11, 10, 0, 0, 123456789, time.UTC),
		time.Date(2026, 9, 11, 15, 30, 0, 0, time.FixedZone("IST", 5*3600+1800)),
	} {
		e := event(t, events.EventSessionSignal, map[string]any{"name": "handoff"})
		e.OccurredAt = ts
		if err := e.Validate(); err != nil {
			t.Errorf("timestamp %s was rejected: %v", ts.Format(time.RFC3339Nano), err)
		}
	}
}

func TestEventRejectsANonOpaqueSessionID(t *testing.T) {
	t.Parallel()
	e := event(t, events.EventSessionSignal, map[string]any{"name": "handoff"})
	e.SessionID = "call-with-jane@example.com"
	if err := e.Validate(); err == nil {
		t.Fatal("an identifying session id reached an event; these land in vendor dashboards")
	}
}

func TestGeneratedEnumsMatchSchema(t *testing.T) {
	t.Parallel()
	const file = "events/v1/envelope.schema.json"
	cases := []struct {
		name    string
		pointer []string
		got     []string
	}{
		{"EventType", []string{"$defs", "EventType", "enum"}, schema.Names(events.AllEventTypes)},
		{"AgentState", []string{"$defs", "AgentState", "enum"}, schema.Names(events.AllAgentStates)},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			if err := schema.CheckEnum(file, tc.pointer, tc.got); err != nil {
				t.Error(err)
			}
		})
	}
}
