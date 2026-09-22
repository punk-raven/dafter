package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"go/format"
	"os"
	"path/filepath"
	"strings"
)

type target struct {
	Schema  string   // path under schemas/
	Pointer []string // where the enum lives in that schema
	Package string   // Go package name
	Type    string   // Go type name
	Prefix  string   // constant name prefix
	Out     string   // file to write, relative to the go module root
	AllName string   // name of the generated slice of every member
	TypeDoc string
}

var targets = []target{
	{
		Schema: "errors/v1/error.schema.json", Pointer: []string{"$defs", "ErrorCode", "enum"},
		Package: "errs", Type: "ErrorCode", Prefix: "Code",
		Out: "internal/errs/codes_gen.go", AllName: "AllErrorCodes",
	},
	{
		Schema: "errors/v1/error.schema.json", Pointer: []string{"properties", "stage", "enum"},
		Package: "errs", Type: "Stage", Prefix: "Stage",
		Out: "internal/errs/stages_gen.go", AllName: "AllStages",
	},
	{
		Schema: "events/v1/envelope.schema.json", Pointer: []string{"$defs", "EventType", "enum"},
		Package: "events", Type: "EventType", Prefix: "Event",
		Out: "internal/events/types_gen.go", AllName: "AllEventTypes",
	},
	{
		Schema: "events/v1/envelope.schema.json", Pointer: []string{"$defs", "AgentState", "enum"},
		Package: "events", Type: "AgentState", Prefix: "Agent",
		Out: "internal/events/states_gen.go", AllName: "AllAgentStates",
	},
	{
		Schema: "common/v1/ids.schema.json", Pointer: []string{"$defs", "Role", "enum"},
		Package: "config", Type: "Role", Prefix: "Role",
		Out: "internal/config/roles_gen.go", AllName: "AllRoles",
	},
	{
		Schema: "common/v1/ids.schema.json", Pointer: []string{"$defs", "Channel", "enum"},
		Package: "config", Type: "Channel", Prefix: "Channel",
		Out: "internal/config/channels_gen.go", AllName: "AllChannels",
	},
	{
		Schema: "config/v1/resolved-session-config.schema.json", Pointer: []string{"properties", "privacyMode", "enum"},
		Package: "config", Type: "PrivacyMode", Prefix: "Privacy",
		Out: "internal/config/privacy_gen.go", AllName: "AllPrivacyModes",
	},
	{
		Schema: "config/v1/resolved-session-config.schema.json", Pointer: []string{"properties", "agent", "properties", "mode", "enum"},
		Package: "config", Type: "AgentMode", Prefix: "Mode",
		Out: "internal/config/modes_gen.go", AllName: "AllAgentModes",
	},
	{
		Schema: "config/v1/resolved-session-config.schema.json", Pointer: []string{"$defs", "Turn", "properties", "strategy", "enum"},
		Package: "config", Type: "TurnStrategy", Prefix: "Turn",
		Out: "internal/config/turn_gen.go", AllName: "AllTurnStrategies",
	},
	{
		Schema: "config/v1/resolved-session-config.schema.json", Pointer: []string{"$defs", "VideoProfile", "properties", "codec", "enum"},
		Package: "config", Type: "VideoCodec", Prefix: "Codec",
		Out: "internal/config/codecs_gen.go", AllName: "AllVideoCodecs",
	},
	{
		Schema: "config/v1/resolved-session-config.schema.json", Pointer: []string{"$defs", "VideoProfile", "properties", "resolution", "enum"},
		Package: "config", Type: "VideoResolution", Prefix: "Resolution",
		Out: "internal/config/resolutions_gen.go", AllName: "AllVideoResolutions",
	},
	{
		Schema: "config/v1/resolved-session-config.schema.json", Pointer: []string{"$defs", "AudioProfile", "properties", "noiseCancellation", "enum"},
		Package: "config", Type: "NoiseCancellation", Prefix: "Noise",
		Out: "internal/config/noise_gen.go", AllName: "AllNoiseCancellations",
	},
	{
		Schema: "config/v1/resolved-session-config.schema.json", Pointer: []string{"$defs", "EgressProfile", "properties", "preset", "enum"},
		Package: "config", Type: "EgressPreset", Prefix: "Preset",
		Out: "internal/config/presets_gen.go", AllName: "AllEgressPresets",
	},
	{
		Schema: "config/v1/resolved-session-config.schema.json", Pointer: []string{"$defs", "EgressProfile", "properties", "videoCodec", "enum"},
		Package: "config", Type: "EgressVideoCodec", Prefix: "EgressCodec",
		Out: "internal/config/egress_codecs_gen.go", AllName: "AllEgressVideoCodecs",
	},
	{
		Schema: "config/v1/resolved-session-config.schema.json", Pointer: []string{"$defs", "Recording", "properties", "layout", "enum"},
		Package: "config", Type: "EgressLayout", Prefix: "Layout",
		Out: "internal/config/layouts_gen.go", AllName: "AllEgressLayouts",
	},
	{
		Schema: "config/v1/resolved-session-config.schema.json", Pointer: []string{"$defs", "Recording", "properties", "startAt", "enum"},
		Package: "config", Type: "RecordingStart", Prefix: "StartAt",
		Out: "internal/config/start_gen.go", AllName: "AllRecordingStarts",
	},
}

var initialisms = map[string]string{
	"vad": "VAD", "stt": "STT", "tts": "TTS", "llm": "LLM", "mt": "MT",
	"webrtc": "WebRTC", "api": "API", "id": "ID", "url": "URL", "json": "JSON",
}

func goName(prefix, value string) string {
	parts := strings.FieldsFunc(value, func(r rune) bool {
		return r == '_' || r == '.' || r == '-'
	})
	var b strings.Builder
	b.WriteString(prefix)
	for _, p := range parts {
		if up, ok := initialisms[strings.ToLower(p)]; ok {
			b.WriteString(up)
			continue
		}
		b.WriteString(strings.ToUpper(p[:1]) + strings.ToLower(p[1:]))
	}
	return b.String()
}

func enumAt(root string, t target) ([]string, error) {
	b, err := os.ReadFile(filepath.Join(root, t.Schema))
	if err != nil {
		return nil, err
	}
	var doc any
	if err := json.Unmarshal(b, &doc); err != nil {
		return nil, err
	}
	cur := doc
	for _, k := range t.Pointer {
		m, ok := cur.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("%s: %v is not an object at %q", t.Schema, t.Pointer, k)
		}
		cur, ok = m[k]
		if !ok {
			return nil, fmt.Errorf("%s: no %q in %v", t.Schema, k, t.Pointer)
		}
	}
	raw, ok := cur.([]any)
	if !ok {
		return nil, fmt.Errorf("%s: %v is not an enum", t.Schema, t.Pointer)
	}
	out := make([]string, 0, len(raw))
	for _, v := range raw {
		s, ok := v.(string)
		if !ok {
			return nil, fmt.Errorf("%s: %v has a non-string member", t.Schema, t.Pointer)
		}
		out = append(out, s)
	}
	if len(out) == 0 {
		return nil, fmt.Errorf("%s: %v is empty", t.Schema, t.Pointer)
	}
	return out, nil
}

func render(t target, values []string) ([]byte, error) {
	var b bytes.Buffer
	fmt.Fprintf(&b, "// Code generated by tools/enumgen from schemas/%s. DO NOT EDIT.\n\n", t.Schema)
	fmt.Fprintf(&b, "package %s\n\n", t.Package)
	fmt.Fprintf(&b, "type %s string\n\n", t.Type)
	fmt.Fprintf(&b, "const (\n")
	for _, v := range values {
		fmt.Fprintf(&b, "\t%s %s = %q\n", goName(t.Prefix, v), t.Type, v)
	}
	fmt.Fprintf(&b, ")\n\n")
	fmt.Fprintf(&b, "// %s is every member, in schema order.\n", t.AllName)
	fmt.Fprintf(&b, "var %s = []%s{\n", t.AllName, t.Type)
	for _, v := range values {
		fmt.Fprintf(&b, "\t%s,\n", goName(t.Prefix, v))
	}
	fmt.Fprintf(&b, "}\n\n")
	fmt.Fprintf(&b, "func (v %s) Valid() bool {\n\tfor _, m := range %s {\n\t\tif v == m {\n\t\t\treturn true\n\t\t}\n\t}\n\treturn false\n}\n", t.Type, t.AllName)
	return format.Source(b.Bytes())
}

func pyName(value string) string {
	var b strings.Builder
	for _, r := range value {
		switch {
		case r >= 'a' && r <= 'z':
			b.WriteRune(r - 32)
		case r >= 'A' && r <= 'Z' || r >= '0' && r <= '9':
			b.WriteRune(r)
		default:
			b.WriteByte('_')
		}
	}
	return b.String()
}

func renderPython(all map[string][]string, order []target) []byte {
	var b bytes.Buffer
	b.WriteString("\"\"\"Generated by tools/enumgen from schemas/. DO NOT EDIT.\"\"\"\n\n")
	b.WriteString("from __future__ import annotations\n\n")
	b.WriteString("from enum import StrEnum\n\n")
	seen := map[string]bool{}
	for _, t := range order {
		if seen[t.Type] {
			continue
		}
		seen[t.Type] = true
		fmt.Fprintf(&b, "\nclass %s(StrEnum):\n", t.Type)
		for _, v := range all[t.Type] {
			fmt.Fprintf(&b, "    %s = %q\n", pyName(v), v)
		}
	}
	return b.Bytes()
}

// run writes every target and reports one line per file written.
func run(schemaRoot, goRoot, pyOut string) ([]string, error) {
	collected := map[string][]string{}
	var report []string

	for _, t := range targets {
		values, err := enumAt(schemaRoot, t)
		if err != nil {
			return nil, err
		}
		src, err := render(t, values)
		if err != nil {
			return nil, fmt.Errorf("render %s: %w", t.Out, err)
		}
		out := filepath.Join(goRoot, t.Out)
		if err := os.WriteFile(out, src, 0o644); err != nil {
			return nil, fmt.Errorf("write %s: %w", out, err)
		}
		collected[t.Type] = values
		report = append(report, fmt.Sprintf("  %-34s %2d values", t.Out, len(values)))
	}

	if err := os.WriteFile(pyOut, renderPython(collected, targets), 0o644); err != nil {
		return nil, fmt.Errorf("write %s: %w", pyOut, err)
	}
	report = append(report, fmt.Sprintf("  %-34s %2d enums", pyOut, len(collected)))
	return report, nil
}

func main() {
	if len(os.Args) != 4 {
		fmt.Fprintln(os.Stderr, "usage: enumgen <schemas-dir> <go-module-root> <python-enums-file>")
		os.Exit(2)
	}
	report, err := run(os.Args[1], os.Args[2], os.Args[3])
	if err != nil {
		fmt.Fprintf(os.Stderr, "enumgen: %v\n", err)
		os.Exit(1)
	}
	fmt.Println(strings.Join(report, "\n"))
}
