package config_test

import (
	"encoding/json"
	"errors"
	"os"
	"regexp"
	"slices"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

const transcriptionVectors = "../../../testdata/transcription/rules.json"

type transcriptionCase struct {
	Name     string                     `json:"name"`
	Patch    map[string]json.RawMessage `json:"patch"`
	Mode     config.TranscriptionMode   `json:"mode"`
	Rejected *struct {
		Code     errs.ErrorCode `json:"code"`
		Pointers []string       `json:"pointers"`
	} `json:"rejected"`
}

type transcriptionVectorFile struct {
	Base  map[string]json.RawMessage `json:"base"`
	Batch json.RawMessage            `json:"batch"`
	Cases []transcriptionCase        `json:"cases"`
}

var detailPointer = regexp.MustCompile(`^at '([^']*)'`)

func (f transcriptionVectorFile) document(t *testing.T, c transcriptionCase) []byte {
	t.Helper()
	doc := map[string]json.RawMessage{}
	for k, v := range f.Base {
		doc[k] = v
	}
	for k, v := range c.Patch {
		doc[k] = v
	}
	raw, err := json.Marshal(doc)
	if err != nil {
		t.Fatal(err)
	}
	var generic map[string]any
	if err := json.Unmarshal(raw, &generic); err != nil {
		t.Fatal(err)
	}
	var batch any
	if err := json.Unmarshal(f.Batch, &batch); err != nil {
		t.Fatal(err)
	}
	if tr, ok := generic["transcription"].(map[string]any); ok && tr["batch"] == "$batch" {
		tr["batch"] = batch
	}
	raw, err = json.Marshal(generic)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestTranscriptionRulesMatchTheSharedVectors(t *testing.T) {
	t.Parallel()
	raw, err := os.ReadFile(transcriptionVectors)
	if err != nil {
		t.Fatal(err)
	}
	var f transcriptionVectorFile
	if err := json.Unmarshal(raw, &f); err != nil {
		t.Fatal(err)
	}
	for _, c := range f.Cases {
		t.Run(c.Name, func(t *testing.T) {
			t.Parallel()
			cfg, err := config.Parse(f.document(t, c))
			if c.Rejected == nil {
				if err != nil {
					t.Fatalf("rejected: %v", err)
				}
				if got := cfg.TranscriptionMode(); got != c.Mode {
					t.Errorf("mode %s, want %s", got, c.Mode)
				}
				return
			}
			var de *errs.Error
			if !errors.As(err, &de) {
				t.Fatalf("want %s, got %v", c.Rejected.Code, err)
			}
			if de.Code != c.Rejected.Code {
				t.Errorf("code %s, want %s", de.Code, c.Rejected.Code)
			}
			var pointers []string
			for _, d := range de.Details {
				if m := detailPointer.FindStringSubmatch(d); m != nil {
					pointers = append(pointers, m[1])
				}
			}
			if !slices.Equal(pointers, c.Rejected.Pointers) {
				t.Errorf("pointers %v, want %v", pointers, c.Rejected.Pointers)
			}
		})
	}
}

func TestTranscriptionModesSayWhatTheyRun(t *testing.T) {
	t.Parallel()
	cases := map[config.TranscriptionMode][3]bool{
		config.TranscriptionOff:       {false, false, false},
		config.TranscriptionLive:      {true, true, false},
		config.TranscriptionAfterCall: {true, false, true},
		config.TranscriptionBoth:      {true, true, true},
	}
	for mode, want := range cases {
		if got := [3]bool{mode.Transcribes(), mode.Live(), mode.AfterCall()}; got != want {
			t.Errorf("%s: transcribes, live, after call = %v, want %v", mode, got, want)
		}
	}
}
