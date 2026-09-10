package config

import (
	"testing"

	"github.com/punk-raven/dafter/go/internal/schema"
)

func TestGeneratedEnumsMatchSchema(t *testing.T) {
	const (
		ids = "common/v1/ids.schema.json"
		cfg = "config/v1/resolved-session-config.schema.json"
	)
	cases := []struct {
		name    string
		file    string
		pointer []string
		got     []string
	}{
		{"Role", ids, []string{"$defs", "Role", "enum"}, schema.Names(AllRoles)},
		{"Channel", ids, []string{"$defs", "Channel", "enum"}, schema.Names(AllChannels)},
		{"PrivacyMode", cfg, []string{"properties", "privacyMode", "enum"}, schema.Names(AllPrivacyModes)},
		{"AgentMode", cfg, []string{"properties", "agent", "properties", "mode", "enum"}, schema.Names(AllAgentModes)},
		{"TurnStrategy", cfg, []string{"$defs", "Turn", "properties", "strategy", "enum"}, schema.Names(AllTurnStrategies)},
		{"EgressLayout", cfg, []string{"$defs", "Recording", "properties", "layout", "enum"}, schema.Names(AllEgressLayouts)},
		{"RecordingStart", cfg, []string{"$defs", "Recording", "properties", "startAt", "enum"}, schema.Names(AllRecordingStarts)},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if err := schema.CheckEnum(tc.file, tc.pointer, tc.got); err != nil {
				t.Error(err)
			}
		})
	}
}
