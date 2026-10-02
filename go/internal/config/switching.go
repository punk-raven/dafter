package config

import "slices"

type LanguageSwitching struct {
	Enabled       bool     `json:"enabled"`
	Languages     []string `json:"languages,omitempty"`
	MinConfidence *float64 `json:"minConfidence,omitempty"`
	MinWords      int      `json:"minWords,omitempty"`
}

func (l *LanguageSwitching) LeavesOut(language string) bool {
	return l != nil && l.Enabled && !slices.Contains(l.Languages, language)
}
