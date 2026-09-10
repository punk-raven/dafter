package events_test

import (
	"testing"

	"github.com/punk-raven/dafter/go/internal/events"
	"github.com/punk-raven/dafter/go/internal/schema"
)

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
