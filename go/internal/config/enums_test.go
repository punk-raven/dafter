package config_test

import (
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/schema"
)

func TestGeneratedEnumsMatchSchema(t *testing.T) {
	t.Parallel()
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
		{"Role", ids, []string{"$defs", "Role", "enum"}, schema.Names(config.AllRoles)},
		{"Channel", ids, []string{"$defs", "Channel", "enum"}, schema.Names(config.AllChannels)},
		{"PrivacyMode", cfg, []string{"properties", "privacyMode", "enum"}, schema.Names(config.AllPrivacyModes)},
		{"AgentMode", cfg, []string{"properties", "agent", "properties", "mode", "enum"}, schema.Names(config.AllAgentModes)},
		{"TurnStrategy", cfg, []string{"$defs", "Turn", "properties", "strategy", "enum"}, schema.Names(config.AllTurnStrategies)},
		{"EgressLayout", cfg, []string{"$defs", "Recording", "properties", "layout", "enum"}, schema.Names(config.AllEgressLayouts)},
		{"RecordingStart", cfg, []string{"$defs", "Recording", "properties", "startAt", "enum"}, schema.Names(config.AllRecordingStarts)},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			t.Parallel()
			if err := schema.CheckEnum(tc.file, tc.pointer, tc.got); err != nil {
				t.Error(err)
			}
		})
	}
}
