package events_test

import (
	"encoding/json"
	"errors"
	"os"
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

func TestADormantAgentNamesNobody(t *testing.T) {
	t.Parallel()
	for _, payload := range []map[string]any{
		{"state": string(events.AgentListening), "dormant": true},
		{"state": string(events.AgentListening), "dormant": false, "wokenBy": "p_4b81e0d7", "wokenVia": string(events.WakeManual)},
	} {
		if err := event(t, events.EventAgentStateChanged, payload).Validate(); err != nil {
			t.Errorf("%v was rejected: %v", payload, err)
		}
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

const vectors = "../../../testdata/events/"

func vector(t *testing.T, name string) map[string]any {
	t.Helper()
	raw, err := os.ReadFile(vectors + name)
	if err != nil {
		t.Fatal(err)
	}
	var doc map[string]any
	if err := json.Unmarshal(raw, &doc); err != nil {
		t.Fatal(err)
	}
	return doc
}

func TestMeasurementVectorsParse(t *testing.T) {
	t.Parallel()
	for name, want := range map[string]events.EventType{
		"agent-state-changed.json": events.EventAgentStateChanged,
		"agent-turn-metrics.json":  events.EventAgentTurnMetrics,
		"session-usage.json":       events.EventSessionUsage,
	} {
		raw, err := os.ReadFile(vectors + name)
		if err != nil {
			t.Fatal(err)
		}
		e, err := events.Parse(raw)
		if err != nil {
			t.Fatalf("%s was rejected: %v", name, err)
		}
		if e.Type != want {
			t.Errorf("%s parsed as %s", name, e.Type)
		}
	}
}

func TestMeasurementPayloadsAreEnforced(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name   string
		file   string
		mutate func(payload map[string]any)
	}{
		{"negative layer", "agent-turn-metrics.json", func(p map[string]any) { p["e2eLatencyMs"] = -1 }},
		{"fractional layer", "agent-turn-metrics.json", func(p map[string]any) { p["llmNodeTtftMs"] = 1.5 }},
		{"negative endpoint", "agent-turn-metrics.json", func(p map[string]any) { p["endpointMs"] = -1 }},
		{"fractional reply gap", "agent-turn-metrics.json", func(p map[string]any) { p["replyGapMs"] = 2.5 }},
		{"unknown layer", "agent-turn-metrics.json", func(p map[string]any) { p["vadDelayMs"] = 10 }},
		{"missing turn", "agent-turn-metrics.json", func(p map[string]any) { delete(p, "turn") }},
		{"serial not a boolean", "agent-turn-metrics.json", func(p map[string]any) { p["serial"] = "yes" }},
		{"serial without its layers", "agent-turn-metrics.json", func(p map[string]any) { delete(p, "llmNodeTtfsMs") }},
		{"unpriced item with a cost", "session-usage.json", func(p map[string]any) { item(p, 1)["costInr"] = 0 }},
		{"priced item without a cost", "session-usage.json", func(p map[string]any) { delete(item(p, 0), "costInr") }},
		{"unknown unit", "session-usage.json", func(p map[string]any) { item(p, 0)["unit"] = "minute" }},
		{"vendor spelling of a provider", "session-usage.json", func(p map[string]any) { item(p, 0)["provider"] = "Sarvam" }},
		{"missing final", "session-usage.json", func(p map[string]any) { delete(p, "final") }},
		{"awake without who woke it", "agent-state-changed.json", func(p map[string]any) { delete(p, "wokenBy"); delete(p, "wokenVia") }},
		{"dormant yet woken", "agent-state-changed.json", func(p map[string]any) { p["dormant"] = true }},
		{"woken without dormant", "agent-state-changed.json", func(p map[string]any) { delete(p, "dormant") }},
		{"woken by a name", "agent-state-changed.json", func(p map[string]any) { p["wokenBy"] = "Asha" }},
		{"woken without how", "agent-state-changed.json", func(p map[string]any) { delete(p, "wokenVia") }},
		{"unknown wake source", "agent-state-changed.json", func(p map[string]any) { p["wokenVia"] = "wake_word" }},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			doc := vector(t, tc.file)
			tc.mutate(doc["payload"].(map[string]any))
			raw, err := json.Marshal(doc)
			if err != nil {
				t.Fatal(err)
			}
			_, err = events.Parse(raw)
			var de *errs.Error
			if !errors.As(err, &de) || de.Code != errs.CodeInternal || len(de.Details) == 0 {
				t.Fatalf("want a located %s, got %v", errs.CodeInternal, err)
			}
		})
	}
}

func item(payload map[string]any, i int) map[string]any {
	return payload["items"].([]any)[i].(map[string]any)
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
		{"UsageUnit", []string{"$defs", "UsageUnit", "enum"}, schema.Names(events.AllUsageUnits)},
		{"WakeSource", []string{"$defs", "WakeSource", "enum"}, schema.Names(events.AllWakeSources)},
		{"SpeakerKind", []string{"$defs", "SpeakerKind", "enum"}, schema.Names(events.AllSpeakerKinds)},
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
