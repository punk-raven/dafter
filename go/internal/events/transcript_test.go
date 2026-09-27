package events_test

import (
	"encoding/json"
	"errors"
	"os"
	"testing"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/events"
)

func TestTranscriptVectorsParse(t *testing.T) {
	t.Parallel()
	for name, want := range map[string]events.EventType{
		"transcript-partial.json":         events.EventTranscriptPartial,
		"transcript-final.json":           events.EventTranscriptFinal,
		"transcript-version-created.json": events.EventTranscriptVersionCreated,
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

func line(p map[string]any, rendering string, i int) map[string]any {
	return p[rendering].([]any)[i].(map[string]any)
}

func provenance(p map[string]any) map[string]any {
	return p["provenance"].(map[string]any)
}

func TestTranscriptPayloadsAreEnforced(t *testing.T) {
	t.Parallel()
	const (
		partial = "transcript-partial.json"
		final   = "transcript-final.json"
		version = "transcript-version-created.json"
	)
	cases := []struct {
		name   string
		file   string
		mutate func(payload map[string]any)
	}{
		{"human without a participant", partial, func(p map[string]any) { delete(p["speaker"].(map[string]any), "participantId") }},
		{"human named", partial, func(p map[string]any) { p["speaker"].(map[string]any)["participantId"] = "Asha" }},
		{"agent with a participant id", final, func(p map[string]any) { p["speaker"].(map[string]any)["participantId"] = "p_4b81e0d7" }},
		{"unknown speaker kind", final, func(p map[string]any) { p["speaker"].(map[string]any)["kind"] = "bot" }},
		{"segment id not opaque", partial, func(p map[string]any) { p["segmentId"] = "segment-1" }},
		{"missing text", partial, func(p map[string]any) { delete(p, "text") }},
		{"source without a model", partial, func(p map[string]any) { delete(p["source"].(map[string]any), "model") }},
		{"unknown caption field", final, func(p map[string]any) { p["speakerName"] = "Nivya" }},
		{"not the batch pass", version, func(p map[string]any) { p["pass"] = "realtime" }},
		{"no transcript hash", version, func(p map[string]any) { delete(p, "transcriptHash") }},
		{"no recordings", version, func(p map[string]any) { provenance(p)["recordings"] = []any{} }},
		{"no config hash", version, func(p map[string]any) { delete(provenance(p), "configHash") }},
		{"a recording the media server could not mint", version, func(p map[string]any) { line(p, "verbatim", 0)["recordingId"] = "r_1d05fa73" }},
		{"negative offset", version, func(p map[string]any) { line(p, "clean", 1)["startMs"] = -5 }},
		{"no clean rendering", version, func(p map[string]any) { delete(p, "clean") }},
		{"line without a speaker", version, func(p map[string]any) { delete(line(p, "verbatim", 1), "speaker") }},
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
