package events

import (
	"bytes"
	"encoding/json"
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

func Parse(raw []byte) (*EventEnvelope, error) {
	if err := schema.ValidateDocument(schema.EventEnvelope, raw, errs.CodeInternal); err != nil {
		return nil, err
	}
	var e EventEnvelope
	d := json.NewDecoder(bytes.NewReader(raw))
	d.DisallowUnknownFields()
	if err := d.Decode(&e); err != nil {
		return nil, errs.Wrap(errs.CodeInternal, err, "decode event envelope")
	}
	return &e, nil
}
