package config_test

import (
	"encoding/json"
	"errors"
	"os"
	"slices"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

const scribeVectors = "../../../testdata/scribe/rules.json"

type scribeCase struct {
	Name     string                     `json:"name"`
	Patch    map[string]json.RawMessage `json:"patch"`
	Enabled  bool                       `json:"enabled"`
	Pool     string                     `json:"pool"`
	Rejected *struct {
		Code     errs.ErrorCode `json:"code"`
		Pointers []string       `json:"pointers"`
	} `json:"rejected"`
}

type scribeVectorFile struct {
	Base  map[string]json.RawMessage `json:"base"`
	LLM   json.RawMessage            `json:"llm"`
	Cases []scribeCase               `json:"cases"`
}

func (f scribeVectorFile) document(t *testing.T, c scribeCase) []byte {
	t.Helper()
	doc := map[string]any{}
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
	var llm any
	if err := json.Unmarshal(f.LLM, &llm); err != nil {
		t.Fatal(err)
	}
	if sc, ok := generic["scribe"].(map[string]any); ok {
		for _, field := range []string{"llm", "judge"} {
			if sc[field] == "$llm" {
				sc[field] = llm
			}
		}
	}
	raw, err = json.Marshal(generic)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func TestScribeRulesMatchTheSharedVectors(t *testing.T) {
	t.Parallel()
	raw, err := os.ReadFile(scribeVectors)
	if err != nil {
		t.Fatal(err)
	}
	var f scribeVectorFile
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
				if cfg.ScribeEnabled() != c.Enabled || cfg.ScribePool() != c.Pool {
					t.Errorf("enabled %v in %s, want %v in %s", cfg.ScribeEnabled(), cfg.ScribePool(), c.Enabled, c.Pool)
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

func TestResolveRefusesAnOverrideThatPointsTheScribeAtAHostOrACredential(t *testing.T) {
	t.Parallel()
	req := request()
	req.Overrides = json.RawMessage(`{"scribe": {
		"llm": {"provider": "openai_compat", "model": "x",
			"credentialRef": "secret://tenants/t_9c21a4be/dafter/worker-secret",
			"options": {"endpoint": "openai"}},
		"judge": {"provider": "openai_compat", "model": "x", "options": {"baseUrl": "https://attacker.example/v1"}}
	}}`)
	de := resolveError(t, req)
	if de.Code != errs.CodeInvalidConfig {
		t.Errorf("want %s, got %s", errs.CodeInvalidConfig, de.Code)
	}
	joined := strings.Join(de.Details, "\n")
	for _, pointer := range []string{
		"/scribe/llm/credentialRef",
		"/scribe/llm/options/endpoint",
		"/scribe/judge/options/baseUrl",
	} {
		if !strings.Contains(joined, pointer) {
			t.Errorf("no detail points at %s; where the scribe's LLM connects is the operator's\n%v", pointer, de)
		}
	}
}

func scribeScoringDocument(t *testing.T, scoring string) []byte {
	t.Helper()
	raw, err := os.ReadFile(scribeVectors)
	if err != nil {
		t.Fatal(err)
	}
	var f scribeVectorFile
	if err := json.Unmarshal(raw, &f); err != nil {
		t.Fatal(err)
	}
	scribe := `{"enabled": true, "consentArtifactId": "c", "llm": "$llm"`
	if scoring != "" {
		scribe += `, "scoring": ` + scoring
	}
	return f.document(t, scribeCase{Patch: map[string]json.RawMessage{"scribe": json.RawMessage(scribe + "}")}})
}

func TestScribeScoringIsOffAndCappedByDefault(t *testing.T) {
	t.Parallel()
	cfg, err := config.Parse(scribeScoringDocument(t, ""))
	if err != nil {
		t.Fatal(err)
	}
	if rate, limit := cfg.Scribe.Scoring.RateFor("hi"), cfg.Scribe.Scoring.TurnCap(); rate != 0 || limit != 20 {
		t.Errorf("rate %v cap %d, want 0 and 20", rate, limit)
	}
}

func TestScribeScoringReadsARatePerBaseLanguage(t *testing.T) {
	t.Parallel()
	cfg, err := config.Parse(scribeScoringDocument(t, `{"sampleRate": 0.05, "languageSampleRates": {"kn": 0.25}, "maxTurnsPerSession": 50, "keepFailures": true}`))
	if err != nil {
		t.Fatal(err)
	}
	s := cfg.Scribe.Scoring
	if s.RateFor("kn-IN") != 0.25 || s.RateFor("hi") != 0.05 || s.TurnCap() != 50 || !s.KeepFailures {
		t.Errorf("scoring read as %+v", s)
	}
}

func TestScribeScoringRefusesARateOrCapOutOfRange(t *testing.T) {
	t.Parallel()
	for scoring, pointer := range map[string]string{
		`{"sampleRate": 1.5}`:                     "/scribe/scoring/sampleRate",
		`{"maxTurnsPerSession": 0}`:               "/scribe/scoring/maxTurnsPerSession",
		`{"languageSampleRates": {"hi-IN": 0.5}}`: "/scribe/scoring/languageSampleRates",
		`{"everyTurn": true}`:                     "/scribe/scoring",
	} {
		_, err := config.Parse(scribeScoringDocument(t, scoring))
		var de *errs.Error
		if !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
			t.Errorf("%s: want %s, got %v", scoring, errs.CodeInvalidConfig, err)
			continue
		}
		if !strings.Contains(strings.Join(de.Details, "\n"), pointer) {
			t.Errorf("%s: no detail points at %s\n%v", scoring, pointer, de.Details)
		}
	}
}
