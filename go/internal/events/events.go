package events

import (
	"time"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/schema"
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
