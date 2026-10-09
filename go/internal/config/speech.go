package config

type PhrasesByLanguage map[string][]string

type Backchannel struct {
	Enabled        *bool             `json:"enabled,omitempty"`
	MaxWords       int               `json:"maxWords,omitempty"`
	AnswerWithinMs int               `json:"answerWithinMs,omitempty"`
	Words          PhrasesByLanguage `json:"words,omitempty"`
	Negatives      PhrasesByLanguage `json:"negatives,omitempty"`
	Reviewed       map[string]bool   `json:"reviewed,omitempty"`
}

type Speech struct {
	Normalization SpeechNormalization          `json:"normalization,omitempty"`
	Substitutions map[string]map[string]string `json:"substitutions,omitempty"`
	Fillers       *Fillers                     `json:"fillers,omitempty"`
	Situations    *Situations                  `json:"situations,omitempty"`
	Expressive    *bool                        `json:"expressive,omitempty"`
}

type Fillers struct {
	Enabled *bool             `json:"enabled,omitempty"`
	AfterMs int               `json:"afterMs,omitempty"`
	Phrases PhrasesByLanguage `json:"phrases,omitempty"`
}

type Situations struct {
	Enabled *bool                           `json:"enabled,omitempty"`
	Cues    map[Situation]PhrasesByLanguage `json:"cues,omitempty"`
}
