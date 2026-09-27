package config_test

import (
	"errors"
	"strings"
	"testing"

	"github.com/punk-raven/dafter/go/internal/config"
	"github.com/punk-raven/dafter/go/internal/errs"
)

func refusedAt(t *testing.T, c *config.ResolvedSessionConfig, pointer string) {
	t.Helper()
	var de *errs.Error
	if err := c.Validate(); !errors.As(err, &de) || de.Code != errs.CodeInvalidConfig {
		t.Fatalf("want %s, got %v", errs.CodeInvalidConfig, err)
	}
	if !strings.Contains(strings.Join(de.Details, "\n"), pointer) {
		t.Errorf("no detail points at %s: %v", pointer, de)
	}
}

func TestValidateAcceptsEveryNormalizationAndCuedSituation(t *testing.T) {
	t.Parallel()
	for _, mode := range config.AllSpeechNormalizations {
		c := validConfig(t)
		cues := map[config.Situation]config.PhrasesByLanguage{}
		for _, s := range []config.Situation{config.SituationGreeting, config.SituationConcern} {
			cues[s] = config.PhrasesByLanguage{"hi": {"नमस्ते"}}
		}
		c.Agent.Speech = &config.Speech{Normalization: mode, Situations: &config.Situations{Cues: cues}}
		if err := c.Validate(); err != nil {
			t.Errorf("normalization %s with every cued situation rejected: %v", mode, err)
		}
	}
}

func TestValidateRejectsASituationOrNormalizationNobodyImplements(t *testing.T) {
	t.Parallel()
	c := validConfig(t)
	c.Agent.Speech = &config.Speech{Normalization: "llm"}
	refusedAt(t, c, "/agent/speech/normalization")

	c = validConfig(t)
	c.Agent.Speech = &config.Speech{Situations: &config.Situations{
		Cues: map[config.Situation]config.PhrasesByLanguage{"angry": {"hi": {"गुस्सा"}}},
	}}
	refusedAt(t, c, "/agent/speech/situations/cues")
}

func TestValidateBoundsTheBackchannelLength(t *testing.T) {
	t.Parallel()
	c := validConfig(t)
	c.Turn.Interruption = &config.Interruption{Backchannel: &config.Backchannel{
		MaxWords: 7, Words: config.PhrasesByLanguage{"hi": {"हाँ"}},
	}}
	refusedAt(t, c, "/turn/interruption/backchannel/maxWords")
}
