package events_test

import (
	"errors"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/events"
)

const valid = `{"eventId":"e_0123456789abcdef0123456789abcdef","type":"agent.state_changed",
 "version":1,"sessionId":"s_7f3a9c21","tenantId":"t_9c21a4be","sequence":0,
 "occurredAt":"2026-09-11T10:00:00Z","payload":{"state":"thinking"}}`

func mustFail(t *testing.T, raw string) *errs.Error {
	t.Helper()
	e, err := events.Parse([]byte(raw))
	if err == nil {
		t.Fatalf("accepted a document it should have refused: %+v", e)
	}
	var de *errs.Error
	if !errors.As(err, &de) {
		t.Fatalf("want *errs.Error, got %T: %v", err, err)
	}
	return de
}

func TestParseAcceptsAWellFormedEvent(t *testing.T) {
	t.Parallel()
	e, err := events.Parse([]byte(valid))
	if err != nil {
		t.Fatalf("valid event rejected: %v", err)
	}
	if e.Type != events.EventAgentStateChanged {
		t.Errorf("type = %q", e.Type)
	}
}

func TestParseRejectsAnUnknownEnvelopeField(t *testing.T) {
	t.Parallel()
	raw := strings.Replace(valid, `"version":1`, `"version":1,"nonsense":1`, 1)
	if de := mustFail(t, raw); !strings.Contains(strings.Join(de.Details, "\n"), "nonsense") {
		t.Errorf("the unknown field is not named:\n%v", de)
	}
}

func TestParseRejectsATimestampWithNoOffset(t *testing.T) {
	t.Parallel()
	raw := strings.Replace(valid, `2026-09-11T10:00:00Z`, `2026-09-11T10:00:00`, 1)
	_ = mustFail(t, raw)
}

func TestParseRejectsAWrongPayload(t *testing.T) {
	t.Parallel()
	raw := strings.Replace(valid, `"state":"thinking"`, `"state":"THINKING"`, 1)
	if de := mustFail(t, raw); de.Code != errs.CodeInternal {
		t.Errorf("want %s, got %s", errs.CodeInternal, de.Code)
	}
}

func TestParseKeepsUntypedEventsOpen(t *testing.T) {
	t.Parallel()
	raw := strings.Replace(valid, `"agent.state_changed"`, `"recording.sealed"`, 1)
	raw = strings.Replace(raw, `{"state":"thinking"}`, `{"anything":[1,2]}`, 1)
	if _, err := events.Parse([]byte(raw)); err != nil {
		t.Fatalf("an intentionally untyped event was rejected: %v", err)
	}
}
