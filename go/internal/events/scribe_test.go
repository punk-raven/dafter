package events_test

import (
	"encoding/json"
	"errors"
	"os"
	"testing"

	"github.com/punk-raven/dafter/go/internal/errs"
	"github.com/punk-raven/dafter/go/internal/events"
)

const (
	scribeNotes   = "scribe-notes.json"
	scribeMinutes = "scribe-minutes.json"
	noteTaken     = "agent-note-taken.json"
	turnScored    = "agent-turn-scored.json"
)

func TestScribeVectorsParse(t *testing.T) {
	t.Parallel()
	for name, want := range map[string]events.EventType{
		scribeNotes:   events.EventScribeNotes,
		scribeMinutes: events.EventScribeMinutes,
		noteTaken:     events.EventAgentNoteTaken,
		turnScored:    events.EventAgentTurnScored,
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

func first(p map[string]any, key string) map[string]any {
	return p[key].([]any)[0].(map[string]any)
}

func TestScribePayloadsAreEnforced(t *testing.T) {
	t.Parallel()
	cases := []struct {
		name   string
		file   string
		mutate func(payload map[string]any)
	}{
		{"notes without a revision", scribeNotes, func(p map[string]any) { delete(p, "revision") }},
		{"notes at revision zero", scribeNotes, func(p map[string]any) { p["revision"] = 0 }},
		{"action item without a task", scribeNotes, func(p map[string]any) { delete(first(p, "actionItems"), "task") }},
		{"speaker named rather than opaque", scribeNotes, func(p map[string]any) {
			first(p, "speakers")["speaker"].(map[string]any)["participantId"] = "Ravi"
		}},
		{"speaker with no points", scribeNotes, func(p map[string]any) { first(p, "speakers")["points"] = []any{} }},
		{"note id not opaque", scribeNotes, func(p map[string]any) { first(p, "notes")["noteId"] = "note-1" }},
		{"notes without their llm", scribeNotes, func(p map[string]any) { delete(p, "source") }},
		{"unknown notes field", scribeNotes, func(p map[string]any) { p["transcript"] = "everything" }},
		{"minutes without a cost", scribeMinutes, func(p map[string]any) { delete(p, "costInr") }},
		{"minutes with a score above one", scribeMinutes, func(p map[string]any) {
			p["quality"].(map[string]any)["meanScore"] = 1.5
		}},
		{"note taken without text", noteTaken, func(p map[string]any) { delete(p, "text") }},
		{"note taken by a name", noteTaken, func(p map[string]any) { p["takenBy"] = "Asha" }},
		{"score without criteria", turnScored, func(p map[string]any) { delete(p, "criteria") }},
		{"score and error together", turnScored, func(p map[string]any) { p["error"] = "provider_timeout" }},
		{"a verdict the judge cannot give", turnScored, func(p map[string]any) {
			p["criteria"].(map[string]any)["language"] = "great"
		}},
		{"scored turn quoting the reply", turnScored, func(p map[string]any) { p["text"] = "जी" }},
		{"scored turn version without an arm", turnScored, func(p map[string]any) {
			p["configVersion"] = map[string]any{"id": "support-v3"}
		}},
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

func TestAJudgeThatFailedSaysWhyInsteadOfScoring(t *testing.T) {
	t.Parallel()
	doc := vector(t, turnScored)
	p := doc["payload"].(map[string]any)
	delete(p, "score")
	delete(p, "criteria")
	p["error"] = "provider_timeout"
	raw, err := json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := events.Parse(raw); err != nil {
		t.Fatalf("a judge failure was refused: %v", err)
	}
}

func TestAScoredTurnCarriesTheVersionTheSessionRuns(t *testing.T) {
	t.Parallel()
	doc := vector(t, turnScored)
	doc["payload"].(map[string]any)["configVersion"] = map[string]any{"id": "support-v4", "arm": "candidate"}
	raw, err := json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := events.Parse(raw); err != nil {
		t.Fatalf("a versioned score was refused: %v", err)
	}
}
