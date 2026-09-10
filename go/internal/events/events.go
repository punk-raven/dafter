package events

import (
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/schema"
)

type EventType string

const (
	EventSessionCreated EventType = "session.created"
	EventSessionEnded   EventType = "session.ended"
	EventSessionSignal  EventType = "session.signal"

	EventConnectionEstablished EventType = "connection.established"
	EventConnectionLost        EventType = "connection.lost"
	EventConnectionRestored    EventType = "connection.restored"

	EventAgentDispatched       EventType = "agent.dispatched"
	EventAgentStateChanged     EventType = "agent.state_changed"
	EventAgentHandoffRequested EventType = "agent.handoff_requested"

	EventTranscriptPartial        EventType = "transcript.partial"
	EventTranscriptFinal          EventType = "transcript.final"
	EventTranscriptVersionCreated EventType = "transcript.version_created"
	EventTranslationFinal         EventType = "translation.final"

	EventRecordingStarted   EventType = "recording.started"
	EventRecordingCompleted EventType = "recording.completed"
	EventRecordingSealed    EventType = "recording.sealed"

	EventProviderDegraded   EventType = "provider.degraded"
	EventProviderFailedOver EventType = "provider.failed_over"

	EventPolicyViolation EventType = "policy.violation"
	EventBudgetExceeded  EventType = "budget.exceeded"
)

type AgentState string

const (
	AgentIdle      AgentState = "idle"
	AgentListening AgentState = "listening"
	AgentThinking  AgentState = "thinking"
	AgentSpeaking  AgentState = "speaking"
)

type EventEnvelope struct {
	EventID string    `json:"eventId"`
	Type    EventType `json:"type"`
	Version int       `json:"version"`

	SessionID string `json:"sessionId"`
	TenantID  string `json:"tenantId"`

	Sequence int64 `json:"sequence"`

	OccurredAt time.Time `json:"occurredAt"`
	TraceID    string    `json:"traceId,omitempty"`

	Payload map[string]any `json:"payload,omitempty"`
}

func (e *EventEnvelope) Validate() error {
	return schema.ValidateAgainst(schema.EventEnvelope, e, errs.CodeInternal)
}
