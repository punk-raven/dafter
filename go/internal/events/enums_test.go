package events

import (
	"testing"

	"github.com/punk-raven/dafter/go/internal/schema"
)

func TestGeneratedEnumsMatchSchema(t *testing.T) {
	const file = "events/v1/envelope.schema.json"
	cases := []struct {
		name    string
		pointer []string
		got     []string
	}{
		{"EventType", []string{"$defs", "EventType", "enum"}, schema.Names(AllEventTypes)},
		{"AgentState", []string{"$defs", "AgentState", "enum"}, schema.Names(AllAgentStates)},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if err := schema.CheckEnum(file, tc.pointer, tc.got); err != nil {
				t.Error(err)
			}
		})
	}
}
